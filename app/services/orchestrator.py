"""Orchestrates the QSG -> battlecard pipeline.

Two stages, matching the two isolated subagents in `.claude/agents/`:

1. Extraction (`qsg_extractor`): one document in, one raw `QSGExtraction`
   out. When `ANTHROPIC_API_KEY` is set and the `anthropic` package is
   installed, a real bounded subagent call is made per CLAUDE.md's "never
   dump raw documents into the primary orchestrator" rule. Otherwise (no
   key, package missing, or a flaky API call) we fall back to a
   deterministic regex/heuristic parser, so local staging and the test
   suite never fail hard on missing credentials.
2. Synthesis: two normalized `DeviceSpec` objects in, one `BattlecardResponse`
   out. This stage is fully deterministic/rule-based rather than routed
   through `battlecard_analyst` (that subagent's contract is free-form
   markdown prose, which doesn't map cleanly onto typed response fields like
   integer friction scores).

Raw extraction never validates or classifies IPs/LEDs itself — that
normalization always goes through `app/tools/net_validator.py` and
`app/tools/led_normalizer.py`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from app.models.schemas import (
    CablingPort,
    LEDIndicator,
    ProvisioningMethod as RawProvisioningMethod,
    QSGExtraction,
)
from app.schemas import (
    BattlecardResponse,
    DeviceSpec,
    LEDStatus,
    ProvisioningMethod as ApiProvisioningMethod,
)
from app.tools.led_normalizer import LEDState, normalize_led
from app.tools.net_validator import InvalidNetworkInput, classify_ip

try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only when package absent
    anthropic = None

_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"
_EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"

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
}

_HEADING_PREFIX_RE = re.compile(r"^#+\s*")
_BULLET_PREFIX_RE = re.compile(r"^[-*]\s+")
_TITLE_SUFFIX_RE = re.compile(r"\s*[—-]\s*quick start guide\s*$", re.IGNORECASE)
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
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
    RawProvisioningMethod.TR069: ApiProvisioningMethod.TR069,
    RawProvisioningMethod.DHCP: ApiProvisioningMethod.DHCP,
    RawProvisioningMethod.WEB_GUI: ApiProvisioningMethod.WEB_GUI,
    RawProvisioningMethod.CONSOLE: ApiProvisioningMethod.CONSOLE,
}
# Methods outside DeviceSpec's 4-value enum map to the closest analogue when
# nothing else is available to pick as the "primary" method.
_PRIMARY_FALLBACK_MAP = {
    RawProvisioningMethod.QR_MOBILE_APP: ApiProvisioningMethod.WEB_GUI,
    RawProvisioningMethod.OTHER: ApiProvisioningMethod.CONSOLE,
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
        heading_candidate = _clean_markdown_emphasis(_HEADING_PREFIX_RE.sub("", line.strip()))
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
        if [c.lower() for c in cells] in (["port", "type", "notes"], ["led", "meaning"]):
            continue
        rows.append(cells)
    return rows


def _detect_device_name(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            title = _clean_markdown_emphasis(_HEADING_PREFIX_RE.sub("", stripped))
            return _TITLE_SUFFIX_RE.sub("", title).strip()
    return "Unknown Device"


def _detect_default_ip(text: str) -> Optional[str]:
    match = _IPV4_RE.search(text)
    return match.group(0) if match else None


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
            if method not in seen and pattern.search(sentence):
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

    return QSGExtraction(
        device_name=_detect_device_name(text),
        source_document=source_document,
        default_ip=_detect_default_ip(text),
        provisioning_methods=_detect_provisioning_methods(text),
        required_tools=_parse_bullets(sections.get("required tools", [])),
        cabling_ports=cabling_ports,
        led_indicators=led_indicators,
    )


def _load_agent_system_prompt(agent_file: str) -> str:
    content = (_AGENTS_DIR / agent_file).read_text(encoding="utf-8")
    if content.startswith("---"):
        _, _, rest = content.partition("---\n")
        _, _, rest = rest.partition("\n---\n")
        return rest.strip()
    return content.strip()


def _anthropic_available() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _extract_via_anthropic(document_text: str, source_document: str) -> QSGExtraction:
    client = anthropic.Anthropic()
    system_prompt = _load_agent_system_prompt("qsg_extractor.md")
    response = client.messages.create(
        model=_EXTRACTOR_MODEL,
        max_tokens=2000,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Source document: {source_document}\n\n{document_text}",
            }
        ],
    )
    payload = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    return QSGExtraction.model_validate(json.loads(payload))


def extract_qsg_fields(document_text: str, source_document: str) -> QSGExtraction:
    """Extract raw QSG fields via the qsg_extractor subagent when an
    Anthropic API key is configured, otherwise via a deterministic
    heuristic parser."""
    if _anthropic_available():
        try:
            return _extract_via_anthropic(document_text, source_document)
        except Exception:
            pass  # never fail hard on a flaky/unreachable API - fall back
    return _heuristic_extract_qsg(document_text, source_document)


def _select_primary_provisioning_method(
    methods: list[RawProvisioningMethod],
) -> ApiProvisioningMethod:
    for method in methods:
        if method in _RAW_TO_API_PROVISIONING:
            return _RAW_TO_API_PROVISIONING[method]
    for method in methods:
        if method in _PRIMARY_FALLBACK_MAP:
            return _PRIMARY_FALLBACK_MAP[method]
    return ApiProvisioningMethod.CONSOLE


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
                state=normalized.state,
                color=normalized.color,
                description=led.raw_description,
            )
        )

    ports = [
        f"{port.port_name} ({port.port_type})" for port in extraction.cabling_ports
    ]

    return DeviceSpec(
        model_name=extraction.device_name,
        default_ip=extraction.default_ip,
        is_private_ip=is_private_ip,
        provisioning_method=_select_primary_provisioning_method(
            extraction.provisioning_methods
        ),
        required_tools=extraction.required_tools,
        led_states=led_states,
        ports=ports,
    )


def extract_device_spec(document_text: str, source_document: str) -> DeviceSpec:
    """Full pipeline for one document: raw extraction, then deterministic
    IP/LED normalization into a clean `DeviceSpec`."""
    extraction = extract_qsg_fields(document_text, source_document)
    return _normalize_to_device_spec(extraction)


# ---------------------------------------------------------------------------
# Stage 2: synthesis
# ---------------------------------------------------------------------------

_REMOTE_PROVISIONING_METHODS = {ApiProvisioningMethod.TR069, ApiProvisioningMethod.DHCP}


def _compute_friction_score(spec: DeviceSpec) -> int:
    score = 1
    score += min(len(spec.required_tools), 4)
    if spec.provisioning_method not in _REMOTE_PROVISIONING_METHODS:
        score += 3
    if spec.provisioning_method == ApiProvisioningMethod.CONSOLE:
        score += 2
    return max(1, min(10, score))


def _build_comparison_matrix(device_a: DeviceSpec, device_b: DeviceSpec) -> dict:
    return {
        "model_name": {"device_a": device_a.model_name, "device_b": device_b.model_name},
        "default_ip": {"device_a": device_a.default_ip, "device_b": device_b.default_ip},
        "is_private_ip": {
            "device_a": device_a.is_private_ip,
            "device_b": device_b.is_private_ip,
        },
        "provisioning_method": {
            "device_a": device_a.provisioning_method.value,
            "device_b": device_b.provisioning_method.value,
        },
        "required_tools": {
            "device_a": device_a.required_tools,
            "device_b": device_b.required_tools,
        },
        "ports": {"device_a": device_a.ports, "device_b": device_b.ports},
        "led_states": {
            "device_a": [led.model_dump() for led in device_a.led_states],
            "device_b": [led.model_dump() for led in device_b.led_states],
        },
    }


def _build_key_risks(spec: DeviceSpec) -> list[str]:
    risks = []
    if spec.provisioning_method == ApiProvisioningMethod.CONSOLE:
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
        risks.append("No significant field-deployment risks identified from the extracted spec.")
    return risks


def _build_sales_talk_track(
    device_a: DeviceSpec, score_a: int, device_b: DeviceSpec, score_b: int
) -> str:
    if score_a <= score_b:
        winner, w_score, loser, l_score = device_a.model_name, score_a, device_b.model_name, score_b
    else:
        winner, w_score, loser, l_score = device_b.model_name, score_b, device_a.model_name, score_a
    return (
        f"{winner} installs with meaningfully less field labor than {loser} "
        f"(Installation Friction Index {w_score} vs. {l_score}). Lead with "
        f"faster time-to-online and lower truck-roll/support cost whenever "
        f"{loser} is the incumbent."
    )


def synthesize_battlecard(device_a: DeviceSpec, device_b: DeviceSpec) -> BattlecardResponse:
    """Deterministically compare two normalized `DeviceSpec` objects into a
    `BattlecardResponse`. Rule-based rather than LLM-routed: `battlecard_analyst`
    produces free-form markdown, which doesn't map onto these typed fields."""
    score_a = _compute_friction_score(device_a)
    score_b = _compute_friction_score(device_b)
    return BattlecardResponse(
        comparison_matrix=_build_comparison_matrix(device_a, device_b),
        installation_friction_score_device_a=score_a,
        installation_friction_score_device_b=score_b,
        key_risks_device_a=_build_key_risks(device_a),
        key_risks_device_b=_build_key_risks(device_b),
        sales_talk_track=_build_sales_talk_track(device_a, score_a, device_b, score_b),
    )
