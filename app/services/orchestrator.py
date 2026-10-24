"""Orchestrates the QSG -> battlecard pipeline.

Two stages, matching the two isolated subagents in `.claude/agents/`:

1. Extraction (`qsg_extractor`): one document in, one raw `QSGExtraction`
   out. When an `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` is set (see
   `_select_llm_provider` for which one wins, and `LLM_PROVIDER` to force
   one explicitly) and the matching package is installed, a real bounded
   subagent call is made per CLAUDE.md's "never dump raw documents into the
   primary orchestrator" rule. Otherwise (no usable key/package, or a flaky
   API call) we fall back to a deterministic regex/heuristic parser, so
   local staging and the test suite never fail hard on missing credentials.
2. Synthesis: two normalized `DeviceSpec` objects in, one `BattlecardResponse`
   out. This stage is fully deterministic/rule-based: an LLM's free-form
   prose doesn't map cleanly onto typed response fields like integer friction
   scores.

Raw extraction never validates or classifies IPs/LEDs itself — that
normalization always goes through `app/tools/net_validator.py` and
`app/tools/led_normalizer.py`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from app.models.schemas import (
    CablingPort,
    LEDIndicator,
    QSGExtraction,
    RawProvisioningMethod,
)
from app.schemas import (
    BattlecardResponse,
    DeviceSpec,
    LEDStatus,
    ProvisioningMethod,
)
from app.tools.capability_compare import parse_speed_mbps, rank_wifi_standard
from app.tools.led_normalizer import LEDState, led_sort_key, normalize_led
from app.tools.net_validator import InvalidNetworkInput, classify_ip
from app.tools.port_counter import expand_port_range

try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only when package absent
    anthropic = None  # type: ignore[assignment]

try:
    from google import genai
    from google.genai import types as gemini_types
except ImportError:  # pragma: no cover - exercised only when package absent
    genai = None  # type: ignore[assignment]
    gemini_types = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"
_ANTHROPIC_EXTRACTOR_MODEL = "claude-haiku-5-5"
_GEMINI_EXTRACTOR_MODEL = "gemini-2.5-flash"

# Bounds on the LLM call: untrusted PDF text must not be able to run up an
# unbounded bill or tie up a worker thread on a hung connection.
MAX_LLM_INPUT_CHARS = 100_000
_LLM_MAX_OUTPUT_TOKENS = 4096
_LLM_TIMEOUT_SECONDS = 60.0
_EXTRACTION_TOOL_NAME = "record_qsg_extraction"

EXTRACTION_SOURCE_LLM = "llm"
EXTRACTION_SOURCE_HEURISTIC = "heuristic"

_PROVIDER_ANTHROPIC = "anthropic"
_PROVIDER_GEMINI = "gemini"
_VALID_LLM_PROVIDERS = {_PROVIDER_ANTHROPIC, _PROVIDER_GEMINI}

# ---------------------------------------------------------------------------
# Stage 1: extraction
# ---------------------------------------------------------------------------

_KNOWN_HEADINGS = {
    "overview",
    "what's in the box",
    "required tools",
    "provisioning",
    "cabling & ports",
    "led indicators",
    "installation steps",
    "key features",
    "specifications",
}

_SPEC_LABEL_TO_FIELD = {
    "wi-fi standard": "wifi_standard",
    "wifi standard": "wifi_standard",
    "wan speed": "wan_speed",
    "form factor": "form_factor",
}

_HEADING_PREFIX_RE = re.compile(r"^#+\s*")
_BULLET_PREFIX_RE = re.compile(r"^[-*]\s+")
_TITLE_SUFFIX_RE = re.compile(r"\s*[—-]\s*quick start guide\s*$", re.IGNORECASE)
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)

_DEFAULT_IP_CONTEXT_RE = re.compile(
    r"default|gateway|management|log\s?in|browse|navigate|admin", re.IGNORECASE
)

# A sentence matching this describes the device acting as a DHCP *server* for
# LAN clients, which says nothing about how the device itself is provisioned.
_DHCP_SERVER_RE = re.compile(
    r"dhcp (?:server|pool|range)|lan clients?"
    r"|(?:serves?|assigns?|hands? out)\b.*\bdhcp",
    re.IGNORECASE,
)

_PROVISIONING_KEYWORD_PATTERNS = [
    (re.compile(r"tr-?069", re.IGNORECASE), RawProvisioningMethod.TR069),
    (re.compile(r"\bdhcp\b", re.IGNORECASE), RawProvisioningMethod.DHCP),
    (re.compile(r"web gui", re.IGNORECASE), RawProvisioningMethod.WEB_GUI),
    (re.compile(r"\bconsole\b", re.IGNORECASE), RawProvisioningMethod.CONSOLE),
    (
        re.compile(r"qr\s*/?\s*mobile app|qr code", re.IGNORECASE),
        RawProvisioningMethod.QR_MOBILE_APP,
    ),
    (re.compile(r"dip switch|jumper", re.IGNORECASE), RawProvisioningMethod.OTHER),
]

# Phrases that negate a provisioning method mentioned in the same sentence.
# Deliberately narrow (e.g. excludes "not required") so that "optional but
# supported" isn't mistaken for "unsupported".
_NEGATION_TRIGGERS = (
    "not supported",
    "no longer supported",
    "does not support",
    "no console port",
    "no local cli",
)

_RAW_TO_API_PROVISIONING = {
    RawProvisioningMethod.TR069: ProvisioningMethod.TR069,
    RawProvisioningMethod.DHCP: ProvisioningMethod.DHCP,
    RawProvisioningMethod.WEB_GUI: ProvisioningMethod.WEB_GUI,
    RawProvisioningMethod.CONSOLE: ProvisioningMethod.CONSOLE,
}
# Methods outside DeviceSpec's 4-value enum map to the closest analogue when
# nothing else is available to pick as the "primary" method.
_PRIMARY_FALLBACK_MAP = {
    RawProvisioningMethod.QR_MOBILE_APP: ProvisioningMethod.WEB_GUI,
    RawProvisioningMethod.OTHER: ProvisioningMethod.CONSOLE,
}


def _clean_markdown_emphasis(text: str) -> str:
    """Strip bold/italic '*' wrapping. pymupdf4llm renders headings and
    table headers as e.g. '**Cabling & Ports**', which would otherwise fail
    exact comparisons against plain heading/header names."""
    return text.strip("*").strip()


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_preamble": []}
    current = "_preamble"
    for line in text.splitlines():
        heading_candidate = _clean_markdown_emphasis(
            _HEADING_PREFIX_RE.sub("", line.strip())
        )
        if heading_candidate.lower() in _KNOWN_HEADINGS:
            current = heading_candidate.lower()
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return sections


def _parse_bullets(lines: list[str]) -> list[str]:
    bullets: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _BULLET_PREFIX_RE.match(stripped):
            if current:
                bullets.append(" ".join(current))
            current = [_BULLET_PREFIX_RE.sub("", stripped)]
        elif current:
            current.append(stripped)
    if current:
        bullets.append(" ".join(current))
    return bullets


def _parse_table_rows(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in lines:
        if "|" not in line:
            continue
        cells = [
            _clean_markdown_emphasis(cell.strip())
            for cell in line.strip().strip("|").split("|")
        ]
        if not cells or all(re.fullmatch(r"-+", c) for c in cells):
            continue
        if [c.lower() for c in cells] in (
            ["port", "type", "notes"],
            ["led", "meaning"],
        ):
            continue
        rows.append(cells)
    return rows


def _parse_specifications(lines: list[str]) -> dict[str, str]:
    """Parse "Label: value" bullets from a Specifications section into the
    QSGExtraction field names they map to. Unrecognized labels are ignored
    rather than guessed at."""
    specs: dict[str, str] = {}
    for bullet in _parse_bullets(lines):
        label, sep, value = bullet.partition(":")
        if not sep:
            continue
        field = _SPEC_LABEL_TO_FIELD.get(label.strip().lower())
        if field:
            specs[field] = value.strip()
    return specs


def _detect_device_name(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            title = _clean_markdown_emphasis(_HEADING_PREFIX_RE.sub("", stripped))
            return _TITLE_SUFFIX_RE.sub("", title).strip()
    return "Unknown Device"


def _is_netmask(candidate: str) -> bool:
    """True for a contiguous-bits subnet mask like 255.255.255.0 (or 0.0.0.0)."""
    bits = "".join(f"{int(octet):08b}" for octet in candidate.split("."))
    return "01" not in bits


def _detect_default_ip(text: str) -> Optional[str]:
    """Pick the device's default address: the first IPv4 that isn't a subnet
    mask, preferring one on a line that says it's the device's own address."""
    fallback: Optional[str] = None
    for line in text.splitlines():
        for match in _IPV4_RE.finditer(line):
            candidate = match.group(0)
            if _is_netmask(candidate):
                continue
            if _DEFAULT_IP_CONTEXT_RE.search(line):
                return candidate
            fallback = fallback or candidate
    return fallback


def _detect_provisioning_methods(text: str) -> list[RawProvisioningMethod]:
    """Return methods in first-occurrence document order (a stand-in for
    "primary method first" when we don't have an LLM to judge intent)."""
    ordered: list[RawProvisioningMethod] = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        lowered = sentence.lower()
        if any(trigger in lowered for trigger in _NEGATION_TRIGGERS):
            continue
        for pattern, method in _PROVISIONING_KEYWORD_PATTERNS:
            if method in seen or not pattern.search(sentence):
                continue
            if method == RawProvisioningMethod.DHCP and _DHCP_SERVER_RE.search(
                sentence
            ):
                continue
            seen.add(method)
            ordered.append(method)
    return ordered


def _heuristic_extract_qsg(text: str, source_document: str) -> QSGExtraction:
    sections = _split_sections(text)

    cabling_ports = [
        CablingPort(
            port_name=row[0],
            port_type=row[1],
            notes=row[2] if len(row) > 2 and row[2] else None,
        )
        for row in _parse_table_rows(sections.get("cabling & ports", []))
        if len(row) >= 2
    ]
    led_indicators = [
        LEDIndicator(led_name=row[0], raw_description=row[1])
        for row in _parse_table_rows(sections.get("led indicators", []))
        if len(row) >= 2
    ]

    specs = _parse_specifications(sections.get("specifications", []))

    return QSGExtraction(
        device_name=_detect_device_name(text),
        source_document=source_document,
        default_ip=_detect_default_ip(text),
        provisioning_methods=_detect_provisioning_methods(text),
        required_tools=_parse_bullets(sections.get("required tools", [])),
        cabling_ports=cabling_ports,
        led_indicators=led_indicators,
        wifi_standard=specs.get("wifi_standard"),
        wan_speed=specs.get("wan_speed"),
        form_factor=specs.get("form_factor"),
        capabilities=_parse_bullets(sections.get("key features", [])),
    )


def _load_agent_system_prompt(agent_file: str) -> str:
    content = (_AGENTS_DIR / agent_file).read_text(encoding="utf-8")
    if content.startswith("---"):
        _, _, rest = content.partition("---\n")
        _, _, rest = rest.partition("\n---\n")
        return rest.strip()
    return content.strip()


def _select_llm_provider() -> Optional[str]:
    """Which LLM provider (if any) to use for extraction.

    `LLM_PROVIDER` is an explicit override ("anthropic" or "gemini") for
    when both API keys are configured and you want to force one — e.g. to
    use a free Gemini key for local testing while an Anthropic key stays
    set for production. Without it, whichever key is actually configured
    wins, Anthropic taking precedence if both are set (preserving prior
    default behavior)."""
    override = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if override:
        if override in _VALID_LLM_PROVIDERS:
            return override
        logger.warning(
            "Unrecognized LLM_PROVIDER %r; falling back to auto-detection by API key",
            override,
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _PROVIDER_ANTHROPIC
    if os.environ.get("GEMINI_API_KEY"):
        return _PROVIDER_GEMINI
    return None


def _provider_ready(provider: str) -> bool:
    """Whether the given provider's package is installed and its API key is
    set — an explicit LLM_PROVIDER override that names a provider with
    neither is not silently redirected to the other provider, it just falls
    back to the heuristic parser."""
    if provider == _PROVIDER_ANTHROPIC:
        return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == _PROVIDER_GEMINI:
        return genai is not None and bool(os.environ.get("GEMINI_API_KEY"))
    return False


def _llm_user_content(document_text: str, source_document: str) -> str:
    if len(document_text) > MAX_LLM_INPUT_CHARS:
        logger.warning(
            "Truncating %r from %d to %d chars for LLM extraction",
            source_document,
            len(document_text),
            MAX_LLM_INPUT_CHARS,
        )
        document_text = document_text[:MAX_LLM_INPUT_CHARS]
    return f"Source document: {source_document}\n\n{document_text}"


def _parse_json_payload(raw: str) -> dict:
    """Parse a model's JSON reply, tolerating a surrounding ```json fence."""
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


def _extract_via_anthropic(document_text: str, source_document: str) -> QSGExtraction:
    client = anthropic.Anthropic(timeout=_LLM_TIMEOUT_SECONDS, max_retries=1)
    system_prompt = _load_agent_system_prompt("qsg_extractor.md")
    # Forcing a tool call makes the API return schema-shaped JSON instead of
    # free text we'd have to parse.
    response = client.messages.create(
        model=_ANTHROPIC_EXTRACTOR_MODEL,
        max_tokens=_LLM_MAX_OUTPUT_TOKENS,
        system=system_prompt,
        tools=[
            {
                "name": _EXTRACTION_TOOL_NAME,
                "description": "Record the fields extracted from the document.",
                "input_schema": QSGExtraction.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": _EXTRACTION_TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": _llm_user_content(document_text, source_document),
            }
        ],
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError("LLM output truncated at max_tokens")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return QSGExtraction.model_validate(getattr(block, "input"))
    raise ValueError("LLM response contained no tool_use block")


def _extract_via_gemini(document_text: str, source_document: str) -> QSGExtraction:
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=gemini_types.HttpOptions(timeout=int(_LLM_TIMEOUT_SECONDS * 1000)),
    )
    system_prompt = _load_agent_system_prompt("qsg_extractor.md")
    response = client.models.generate_content(
        model=_GEMINI_EXTRACTOR_MODEL,
        contents=_llm_user_content(document_text, source_document),
        config=gemini_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            max_output_tokens=_LLM_MAX_OUTPUT_TOKENS,
        ),
    )
    return QSGExtraction.model_validate(_parse_json_payload(response.text))


_PROVIDER_EXTRACTORS = {
    _PROVIDER_ANTHROPIC: _extract_via_anthropic,
    _PROVIDER_GEMINI: _extract_via_gemini,
}


def extract_qsg_fields(document_text: str, source_document: str) -> QSGExtraction:
    return _extract_with_source(document_text, source_document)[0]


def _extract_with_source(
    document_text: str, source_document: str
) -> tuple[QSGExtraction, str]:
    """Extract raw QSG fields via a configured LLM provider (Anthropic or
    Gemini — see `_select_llm_provider`) when available, otherwise via a
    deterministic heuristic parser."""
    provider = _select_llm_provider()
    if provider and _provider_ready(provider):
        try:
            return (
                _PROVIDER_EXTRACTORS[provider](document_text, source_document),
                EXTRACTION_SOURCE_LLM,
            )
        except Exception:
            # never fail hard on a flaky/unreachable API - fall back, but
            # log it so a persistently failing key doesn't go unnoticed
            logger.warning(
                "%s extraction failed for %r; falling back to heuristic parser",
                provider.capitalize(),
                source_document,
                exc_info=True,
            )
    elif provider:
        logger.debug(
            "LLM_PROVIDER=%r selected but its package or API key isn't "
            "available; using heuristic parser for %r",
            provider,
            source_document,
        )
    else:
        logger.debug(
            "No LLM provider configured; using heuristic parser for %r",
            source_document,
        )
    return (
        _heuristic_extract_qsg(document_text, source_document),
        EXTRACTION_SOURCE_HEURISTIC,
    )


def _select_primary_provisioning_method(
    methods: list[RawProvisioningMethod],
) -> ProvisioningMethod:
    for method in methods:
        if method in _RAW_TO_API_PROVISIONING:
            return _RAW_TO_API_PROVISIONING[method]
    for method in methods:
        if method in _PRIMARY_FALLBACK_MAP:
            return _PRIMARY_FALLBACK_MAP[method]
    return ProvisioningMethod.CONSOLE


def _normalize_to_device_spec(extraction: QSGExtraction) -> DeviceSpec:
    is_private_ip = False
    if extraction.default_ip:
        try:
            is_private_ip = classify_ip(extraction.default_ip)
        except InvalidNetworkInput:
            is_private_ip = False

    led_states = []
    for led in extraction.led_indicators:
        normalized = normalize_led(led.raw_description)
        led_states.append(
            LEDStatus(
                name=led.led_name,
                state=normalized.state,
                color=normalized.color,
                pattern=normalized.pattern,
                frequency_hz=normalized.frequency_hz,
                description=led.raw_description,
            )
        )
    led_states.sort(key=lambda led: led_sort_key(led.state))

    ports = []
    for port in extraction.cabling_ports:
        label, count = expand_port_range(port.port_name)
        name = f"{count}x {label}" if count > 1 else port.port_name
        entry = f"{name} ({port.port_type})"
        if port.notes:
            entry += f" - {port.notes}"
        ports.append(entry)

    return DeviceSpec(
        model_name=extraction.device_name,
        default_ip=extraction.default_ip,
        is_private_ip=is_private_ip,
        provisioning_method=_select_primary_provisioning_method(
            extraction.provisioning_methods
        ),
        provisioning_methods=[m.value for m in extraction.provisioning_methods],
        required_tools=extraction.required_tools,
        led_states=led_states,
        ports=ports,
        wifi_standard=extraction.wifi_standard,
        wan_speed=extraction.wan_speed,
        form_factor=extraction.form_factor,
        capabilities=extraction.capabilities,
    )


def extract_device_spec(document_text: str, source_document: str) -> DeviceSpec:
    """Full pipeline for one document: raw extraction, then deterministic
    IP/LED normalization into a clean `DeviceSpec`."""
    extraction, source = _extract_with_source(document_text, source_document)
    spec = _normalize_to_device_spec(extraction)
    spec.extraction_source = source
    return spec


def extract_device_specs(
    text_a: str, name_a: str, text_b: str, name_b: str
) -> tuple[DeviceSpec, DeviceSpec]:
    """Run both extractions concurrently; the LLM calls are I/O-bound."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(extract_device_spec, text_a, name_a)
        future_b = pool.submit(extract_device_spec, text_b, name_b)
        return future_a.result(), future_b.result()


# ---------------------------------------------------------------------------
# Stage 2: synthesis
# ---------------------------------------------------------------------------

_REMOTE_PROVISIONING_METHODS = {ProvisioningMethod.TR069, ProvisioningMethod.DHCP}

# Exact text app.js matches on to render these as a neutral "nothing found"
# line instead of a highlighted risk/advantage — keep the two in sync.
NO_RISKS_MESSAGE = (
    "No significant field-deployment risks identified from the extracted spec."
)
NO_ADVANTAGES_MESSAGE = (
    "No differentiating capabilities identified from the extracted spec."
)


def _compute_friction_score(spec: DeviceSpec) -> int:
    score = 1
    score += min(len(spec.required_tools), 4)
    if spec.provisioning_method not in _REMOTE_PROVISIONING_METHODS:
        score += 3
    if spec.provisioning_method == ProvisioningMethod.CONSOLE:
        score += 2
    return max(1, min(10, score))


def _build_comparison_matrix(device_a: DeviceSpec, device_b: DeviceSpec) -> dict:
    return {
        "model_name": {
            "device_a": device_a.model_name,
            "device_b": device_b.model_name,
        },
        "default_ip": {
            "device_a": device_a.default_ip,
            "device_b": device_b.default_ip,
        },
        "is_private_ip": {
            "device_a": device_a.is_private_ip,
            "device_b": device_b.is_private_ip,
        },
        "provisioning_method": {
            "device_a": device_a.provisioning_method.value,
            "device_b": device_b.provisioning_method.value,
        },
        "provisioning_methods": {
            "device_a": device_a.provisioning_methods,
            "device_b": device_b.provisioning_methods,
        },
        "required_tools": {
            "device_a": device_a.required_tools,
            "device_b": device_b.required_tools,
        },
        "ports": {"device_a": device_a.ports, "device_b": device_b.ports},
        "wifi_standard": {
            "device_a": device_a.wifi_standard,
            "device_b": device_b.wifi_standard,
        },
        "wan_speed": {
            "device_a": device_a.wan_speed,
            "device_b": device_b.wan_speed,
        },
        "form_factor": {
            "device_a": device_a.form_factor,
            "device_b": device_b.form_factor,
        },
        "capabilities": {
            "device_a": device_a.capabilities,
            "device_b": device_b.capabilities,
        },
        "led_states": {
            "device_a": [led.model_dump() for led in device_a.led_states],
            "device_b": [led.model_dump() for led in device_b.led_states],
        },
    }


def _build_key_risks(spec: DeviceSpec) -> list[str]:
    risks = []
    if spec.provisioning_method == ProvisioningMethod.CONSOLE:
        risks.append(
            "Requires a technician with a laptop and terminal emulator on-site "
            "for initial console setup."
        )
    if (
        spec.default_ip
        and spec.is_private_ip
        and spec.provisioning_method not in _REMOTE_PROVISIONING_METHODS
    ):
        risks.append(
            f"Static default IP {spec.default_ip} may collide with an existing "
            "LAN subnet during local configuration."
        )
    if any(led.state == LEDState.UNKNOWN for led in spec.led_states):
        risks.append(
            "One or more LED states could not be classified from the spec; "
            "field techs may misread device status."
        )
    if not risks:
        risks.append(NO_RISKS_MESSAGE)
    return risks


def _build_key_advantages(
    device_a: DeviceSpec, device_b: DeviceSpec
) -> tuple[list[str], list[str]]:
    """Deterministically surface competitive pros/cons a sales engineer can
    lead with — Wi-Fi generation, WAN throughput, and any capability one
    device has that the other doesn't. Never ranks physical port counts or
    form_factor, since neither has an objectively "better" value across
    different port/form types."""
    advantages_a: list[str] = []
    advantages_b: list[str] = []

    rank_a = rank_wifi_standard(device_a.wifi_standard)
    rank_b = rank_wifi_standard(device_b.wifi_standard)
    if rank_a is not None and rank_b is not None and rank_a != rank_b:
        if rank_a > rank_b:
            advantages_a.append(
                f"Newer Wi-Fi standard ({device_a.wifi_standard} vs. "
                f"{device_b.wifi_standard})."
            )
        else:
            advantages_b.append(
                f"Newer Wi-Fi standard ({device_b.wifi_standard} vs. "
                f"{device_a.wifi_standard})."
            )

    speed_a = parse_speed_mbps(device_a.wan_speed)
    speed_b = parse_speed_mbps(device_b.wan_speed)
    if speed_a is not None and speed_b is not None and speed_a != speed_b:
        if speed_a > speed_b:
            advantages_a.append(
                f"Faster WAN throughput ({device_a.wan_speed} vs. "
                f"{device_b.wan_speed})."
            )
        else:
            advantages_b.append(
                f"Faster WAN throughput ({device_b.wan_speed} vs. "
                f"{device_a.wan_speed})."
            )

    caps_a_only = [c for c in device_a.capabilities if c not in device_b.capabilities]
    caps_b_only = [c for c in device_b.capabilities if c not in device_a.capabilities]
    advantages_a.extend(f"Unique capability: {c}" for c in caps_a_only)
    advantages_b.extend(f"Unique capability: {c}" for c in caps_b_only)

    if not advantages_a:
        advantages_a.append(NO_ADVANTAGES_MESSAGE)
    if not advantages_b:
        advantages_b.append(NO_ADVANTAGES_MESSAGE)
    return advantages_a, advantages_b


def _build_sales_talk_track(
    device_a: DeviceSpec, score_a: int, device_b: DeviceSpec, score_b: int
) -> str:
    if score_a == score_b:
        return (
            f"{device_a.model_name} and {device_b.model_name} are tied on "
            f"install effort (Installation Friction Index {score_a} each). "
            f"Differentiate on the capability and spec advantages above "
            f"rather than on field labor."
        )
    if score_a < score_b:
        winner, w_score, loser, l_score = (
            device_a.model_name,
            score_a,
            device_b.model_name,
            score_b,
        )
    else:
        winner, w_score, loser, l_score = (
            device_b.model_name,
            score_b,
            device_a.model_name,
            score_a,
        )
    return (
        f"{winner} installs with meaningfully less field labor than {loser} "
        f"(Installation Friction Index {w_score} vs. {l_score}). Lead with "
        f"faster time-to-online and lower truck-roll/support cost whenever "
        f"{loser} is the incumbent."
    )


def synthesize_battlecard(
    device_a: DeviceSpec, device_b: DeviceSpec
) -> BattlecardResponse:
    """Deterministically compare two normalized `DeviceSpec` objects into a
    `BattlecardResponse`. Rule-based rather than LLM-routed, since free-form
    prose doesn't map onto these typed fields."""
    score_a = _compute_friction_score(device_a)
    score_b = _compute_friction_score(device_b)
    advantages_a, advantages_b = _build_key_advantages(device_a, device_b)
    return BattlecardResponse(
        comparison_matrix=_build_comparison_matrix(device_a, device_b),
        installation_friction_score_device_a=score_a,
        installation_friction_score_device_b=score_b,
        key_risks_device_a=_build_key_risks(device_a),
        key_risks_device_b=_build_key_risks(device_b),
        key_advantages_device_a=advantages_a,
        key_advantages_device_b=advantages_b,
        sales_talk_track=_build_sales_talk_track(device_a, score_a, device_b, score_b),
        extraction_source_device_a=device_a.extraction_source,
        extraction_source_device_b=device_b.extraction_source,
    )
