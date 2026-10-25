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
   A request can also bring its own provider and key (`LLMOverride`, sent by
   the dashboard's settings); that key is used for the request only.
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
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Hashable, Iterable, Literal, Optional, TypeVar

from pydantic import ValidationError

from app.models.schemas import (
    DROPPABLE_FIELDS,
    CablingPort,
    DocumentType,
    LEDIndicator,
    QSGExtraction,
    RawProvisioningMethod,
    SpecItem,
)
from app.schemas import (
    BattlecardResponse,
    DeviceSpec,
    LEDStatus,
    Perspective,
    ProvisioningMethod,
    SpecValue,
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
_GEMINI_EXTRACTOR_MODEL = "gemini-2.5-flash-lite"

# Bounds on the LLM call: untrusted PDF text must not be able to run up an
# unbounded bill or tie up a worker thread on a hung connection.
MAX_LLM_INPUT_CHARS = 100_000
_LLM_MAX_OUTPUT_TOKENS = 4096
_LLM_TIMEOUT_SECONDS = 60.0
_EXTRACTION_TOOL_NAME = "record_qsg_extraction"

# Documents longer than MAX_LLM_INPUT_CHARS are read section by section rather
# than truncated: split on Markdown headings into parts of at most this size,
# each sent to the same bounded extractor, then merged deterministically.
_CHUNK_CHARS = 60_000
_MAX_CHUNKS = 8
_CHUNK_WORKERS = 4
# Open-ended spec rows kept per device, so one verbose datasheet can't flood
# the comparison table.
_MAX_SPEC_ROWS = 40

EXTRACTION_SOURCE_LLM = "llm"
EXTRACTION_SOURCE_HEURISTIC = "heuristic"

_PROVIDER_ANTHROPIC = "anthropic"
_PROVIDER_GEMINI = "gemini"
_VALID_LLM_PROVIDERS = {_PROVIDER_ANTHROPIC, _PROVIDER_GEMINI}
_PROVIDER_HEURISTIC = "heuristic"
_PROVIDER_NAMES = {_PROVIDER_ANTHROPIC: "Anthropic", _PROVIDER_GEMINI: "Gemini"}


@dataclass(frozen=True)
class LLMOverride:
    """A request's own choice of extractor, from the dashboard's settings.

    `heuristic` skips the LLM. A provider with `api_key` uses that key for
    this request only; without one it uses the server's key for that
    provider. The key is never stored or logged."""

    provider: Literal["anthropic", "gemini", "heuristic"]
    api_key: Optional[str] = None

    def __repr__(self) -> str:  # keep the key out of tracebacks and logs
        return f"LLMOverride(provider={self.provider!r})"


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
_TITLE_SUFFIX_RE = re.compile(
    r"\s*[—-]\s*(?:quick start guide|data\s*sheet|spec(?:ification)?s? sheet)\s*$",
    re.IGNORECASE,
)
_DATASHEET_TITLE_RE = re.compile(
    r"\b(?:data\s*sheet|spec(?:ification)?s?\s+sheet|product\s+specifications?)\b",
    re.IGNORECASE,
)
_INSTALL_TITLE_RE = re.compile(
    r"\b(?:quick\s*start|installation|install|setup|getting\s+started)\b",
    re.IGNORECASE,
)
_INSTALL_SECTIONS = {"installation steps", "required tools", "provisioning"}
_SPEC_TABLE_HEADERS = {"spec", "specification", "parameter", "feature", "value"}
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
    """Map the Specifications section's labels (bullets or table rows) onto
    the QSGExtraction field names they correspond to. Unrecognized labels are
    ignored here; they become open-ended spec rows instead."""
    specs: dict[str, str] = {}
    for item in _parse_spec_items(lines):
        field = _SPEC_LABEL_TO_FIELD.get(_spec_key(item.name))
        if field and field not in specs:
            specs[field] = item.value
    return specs


def _parse_spec_items(lines: list[str]) -> list[SpecItem]:
    """Every "Label: value" bullet and two-column table row in a
    Specifications section, verbatim, for the open-ended spec rows."""
    items = []
    for bullet in _parse_bullets(lines):
        label, sep, value = bullet.partition(":")
        if sep and label.strip() and value.strip():
            items.append(SpecItem(name=label.strip(), value=value.strip()))
    for row in _parse_table_rows(lines):
        if len(row) < 2 or not row[0] or not row[1]:
            continue
        if {row[0].lower(), row[1].lower()} <= _SPEC_TABLE_HEADERS:
            continue
        items.append(SpecItem(name=row[0], value=row[1]))
    return items


def _detect_document_type(text: str, sections: dict[str, list[str]]) -> DocumentType:
    """Call a document a datasheet only on clear evidence: a datasheet title, or
    a Specifications section with no install title or install sections.
    Everything else stays a quick start guide, as before document types."""
    title = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if _DATASHEET_TITLE_RE.search(title):
        return DocumentType.DATASHEET
    looks_like_install = bool(
        _INSTALL_TITLE_RE.search(title) or _INSTALL_SECTIONS & sections.keys()
    )
    if "specifications" in sections and not looks_like_install:
        return DocumentType.DATASHEET
    return DocumentType.QUICK_START_GUIDE


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
        document_type=_detect_document_type(text, sections),
        specs=_parse_spec_items(sections.get("specifications", [])),
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


def _package_available(provider: str) -> bool:
    if provider == _PROVIDER_ANTHROPIC:
        return anthropic is not None
    if provider == _PROVIDER_GEMINI:
        return genai is not None
    return False


def server_llm_provider() -> Optional[str]:
    """The provider the server's own configuration would use, if any."""
    provider = _select_llm_provider()
    return provider if provider and _provider_ready(provider) else None


def _resolve_llm(llm: Optional[LLMOverride]) -> tuple[Optional[str], Optional[str]]:
    """Return (provider, user API key) for one request. A None provider means
    the heuristic parser; a None key means the server's key from the env."""
    if llm is None:
        provider = _select_llm_provider()
        if provider and not _provider_ready(provider):
            logger.debug(
                "LLM_PROVIDER=%r selected but its package or API key isn't "
                "available; using heuristic parser",
                provider,
            )
            return None, None
        return provider, None
    if llm.provider == _PROVIDER_HEURISTIC:
        return None, None
    if llm.api_key and _package_available(llm.provider):
        return llm.provider, llm.api_key
    if _provider_ready(llm.provider):
        return llm.provider, None
    return None, None


def _extractor_for(
    provider: str, api_key: Optional[str]
) -> Callable[[str, str], QSGExtraction]:
    extractor = _PROVIDER_EXTRACTORS[provider]
    return partial(extractor, api_key=api_key) if api_key else extractor


def _is_auth_error(exc: Exception) -> bool:
    """Whether an SDK error means the key was rejected (both SDKs expose the
    HTTP status; Gemini reports a bad key as a 400)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (401, 403):
        return True
    return status == 400 and "api key" in str(exc).lower()


def _user_key_warning(provider: str, exc: Exception) -> str:
    name = _PROVIDER_NAMES[provider]
    if _is_auth_error(exc):
        return (
            f"The {name} key in your settings was rejected, so this document was "
            "read with the built-in parser. Check the key in Settings."
        )
    return (
        f"The {name} request using your key failed, so this document was read "
        "with the built-in parser. Try again, or check the key in Settings."
    )


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
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"LLM reply was {type(payload).__name__}, not a JSON object")
    return payload


def _validate_extraction(payload: object) -> QSGExtraction:
    """Validate a model's payload against `QSGExtraction`.

    A malformed optional field (the assessment, document type or open-ended
    specs) only costs that field: it is dropped with a warning so the
    otherwise good extraction survives. Any other violation raises, and the
    caller falls back to the heuristic parser."""
    try:
        return QSGExtraction.model_validate(payload)
    except ValidationError as exc:
        bad = {err["loc"][0] for err in exc.errors() if err["loc"]}
        if not isinstance(payload, dict) or not bad <= DROPPABLE_FIELDS:
            raise
        logger.warning("Dropping malformed optional LLM fields: %s", sorted(bad))
        return QSGExtraction.model_validate(
            {k: v for k, v in payload.items() if k not in bad}
        )


def _extract_via_anthropic(
    document_text: str, source_document: str, api_key: Optional[str] = None
) -> QSGExtraction:
    # With no key the SDK reads ANTHROPIC_API_KEY from the environment.
    client = anthropic.Anthropic(
        api_key=api_key, timeout=_LLM_TIMEOUT_SECONDS, max_retries=1
    )
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
            return _validate_extraction(getattr(block, "input"))
    raise ValueError("LLM response contained no tool_use block")


def _extract_via_gemini(
    document_text: str, source_document: str, api_key: Optional[str] = None
) -> QSGExtraction:
    client = genai.Client(
        api_key=api_key or os.environ["GEMINI_API_KEY"],
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
            automatic_function_calling=gemini_types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        ),
    )
    if not response.text:
        raise ValueError("Gemini response contained no text")
    return _validate_extraction(_parse_json_payload(response.text))


_PROVIDER_EXTRACTORS: dict[str, Callable[..., QSGExtraction]] = {
    _PROVIDER_ANTHROPIC: _extract_via_anthropic,
    _PROVIDER_GEMINI: _extract_via_gemini,
}


def extract_qsg_fields(document_text: str, source_document: str) -> QSGExtraction:
    return _extract_with_source(document_text, source_document)[0]


def _extract_with_source(
    document_text: str, source_document: str, llm: Optional[LLMOverride] = None
) -> tuple[QSGExtraction, str]:
    return _extract_single(document_text, source_document, llm)[:2]


def _extract_single(
    document_text: str, source_document: str, llm: Optional[LLMOverride] = None
) -> tuple[QSGExtraction, str, list[str]]:
    """Extract raw QSG fields via the request's or the server's LLM provider
    (Anthropic or Gemini — see `_resolve_llm`) when available, otherwise via a
    deterministic heuristic parser. Also returns warnings for the user."""
    provider, api_key = _resolve_llm(llm)
    warnings: list[str] = []
    if provider:
        try:
            return (
                _extractor_for(provider, api_key)(document_text, source_document),
                EXTRACTION_SOURCE_LLM,
                warnings,
            )
        except Exception as exc:
            # never fail hard on a flaky/unreachable API - fall back, but
            # log it so a persistently failing key doesn't go unnoticed
            logger.warning(
                "%s extraction failed for %r; falling back to heuristic parser",
                provider.capitalize(),
                source_document,
                exc_info=True,
            )
            if api_key:
                warnings.append(_user_key_warning(provider, exc))
    else:
        logger.debug(
            "No LLM provider configured; using heuristic parser for %r",
            source_document,
        )
    return (
        _heuristic_extract_qsg(document_text, source_document),
        EXTRACTION_SOURCE_HEURISTIC,
        warnings,
    )


def _split_into_chunks(text: str, max_chars: int = _CHUNK_CHARS) -> list[str]:
    """Split on Markdown headings and pack whole sections into parts of at
    most `max_chars`. A single oversized section is cut at the last blank line
    that fits, or at `max_chars` if there is none."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("#") and current:
            sections.append("".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("".join(current))

    pieces: list[str] = []
    for section in sections:
        while len(section) > max_chars:
            cut = section.rfind("\n\n", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(section[:cut])
            section = section[cut:]
        pieces.append(section)

    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        if buffer and len(buffer) + len(piece) > max_chars:
            chunks.append(buffer)
            buffer = ""
        buffer += piece
    chunks.append(buffer)
    return [chunk for chunk in chunks if chunk.strip()]


_T = TypeVar("_T")


def _unique(
    items: Iterable[_T], key: Callable[[_T], Hashable] = lambda item: item
) -> list[_T]:
    seen: set[Hashable] = set()
    result = []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _spec_key(name: str) -> str:
    return " ".join(name.lower().rstrip(":").split())


def _merge_extractions(
    parts: list[QSGExtraction], source_document: str
) -> QSGExtraction:
    """Combine per-section extractions in document order: the first non-empty
    scalar wins, and list fields are unioned without duplicates."""

    def first(field: str):
        return next(
            (getattr(p, field) for p in parts if getattr(p, field) not in (None, "")),
            None,
        )

    step_counts = [p.step_count for p in parts if p.step_count is not None]
    return QSGExtraction(
        device_name=parts[0].device_name,
        source_document=source_document,
        default_ip=first("default_ip"),
        provisioning_methods=_unique(m for p in parts for m in p.provisioning_methods),
        required_tools=_unique(
            (tool for p in parts for tool in p.required_tools), key=str.lower
        ),
        cabling_ports=_unique(
            (port for p in parts for port in p.cabling_ports),
            key=lambda port: port.port_name.lower(),
        ),
        led_indicators=_unique(
            (led for p in parts for led in p.led_indicators),
            key=lambda led: (led.led_name.lower(), led.raw_description.lower()),
        ),
        wifi_standard=first("wifi_standard"),
        wan_speed=first("wan_speed"),
        form_factor=first("form_factor"),
        capabilities=_unique(
            (cap for p in parts for cap in p.capabilities), key=str.lower
        ),
        # A procedure normally sits in one section; summing would double-count
        # steps a document repeats.
        step_count=max(step_counts) if step_counts else None,
        setup_difficulty=first("setup_difficulty"),
        strengths=_unique((x for p in parts for x in p.strengths), key=str.lower),
        gaps=_unique((x for p in parts for x in p.gaps), key=str.lower),
        document_type=first("document_type"),
        specs=_unique(
            (spec for p in parts for spec in p.specs),
            key=lambda spec: _spec_key(spec.name),
        ),
    )


def _extract_in_parts(
    extractor: Callable[[str, str], QSGExtraction],
    document_text: str,
    source_document: str,
) -> tuple[QSGExtraction, list[str]]:
    """Run the bounded extractor once per section of a long document and merge.

    The first part runs alone so its device name can be passed to the rest:
    later sections rarely repeat it, and `device_name` is required."""
    chunks = _split_into_chunks(document_text)
    warnings: list[str] = []
    if len(chunks) > _MAX_CHUNKS:
        warnings.append(
            f"Only the first {_MAX_CHUNKS} of {len(chunks)} sections were read; "
            "the document is too long to read in full."
        )
        chunks = chunks[:_MAX_CHUNKS]
    total = len(chunks)
    logger.info("Reading %r in %d parts", source_document, total)
    first = extractor(chunks[0], f"{source_document} (part 1 of {total})")

    def run(index: int) -> QSGExtraction:
        label = (
            f"{source_document} (part {index + 1} of {total}; "
            f"device: {first.device_name})"
        )
        return extractor(chunks[index], label)

    parts = [first]
    failed = 0
    with ThreadPoolExecutor(max_workers=_CHUNK_WORKERS) as pool:
        for future in [pool.submit(run, i) for i in range(1, total)]:
            try:
                parts.append(future.result())
            except Exception:
                failed += 1
                logger.warning(
                    "Extraction failed for a part of %r; skipping it",
                    source_document,
                    exc_info=True,
                )
    if failed:
        warnings.append(
            f"{failed} of {total} sections could not be read and were skipped."
        )
    return _merge_extractions(parts, source_document), warnings


def _extract_document(
    document_text: str, source_document: str, llm: Optional[LLMOverride] = None
) -> tuple[QSGExtraction, str, list[str]]:
    """Like `_extract_single`, but documents too long for one LLM call are
    read in parts instead of being truncated. Also returns warnings about how
    the document was read."""
    provider, api_key = _resolve_llm(llm)
    if provider and len(document_text) > MAX_LLM_INPUT_CHARS:
        try:
            extraction, warnings = _extract_in_parts(
                _extractor_for(provider, api_key), document_text, source_document
            )
            return extraction, EXTRACTION_SOURCE_LLM, warnings
        except Exception as exc:
            logger.warning(
                "%s sectioned extraction failed for %r; falling back to heuristic "
                "parser",
                provider.capitalize(),
                source_document,
                exc_info=True,
            )
            return (
                _heuristic_extract_qsg(document_text, source_document),
                EXTRACTION_SOURCE_HEURISTIC,
                [_user_key_warning(provider, exc)] if api_key else [],
            )
    return _extract_single(document_text, source_document, llm)


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
        step_count=extraction.step_count,
        setup_difficulty=(
            extraction.setup_difficulty.value if extraction.setup_difficulty else None
        ),
        strengths=extraction.strengths,
        gaps=extraction.gaps,
        document_type=(
            extraction.document_type.value if extraction.document_type else None
        ),
        specs=_normalize_specs(extraction.specs),
    )


def _normalize_specs(specs: list[SpecItem]) -> list[SpecValue]:
    """De-duplicate open-ended specs and drop the ones already shown as fixed
    rows (Wi-Fi standard, WAN speed, form factor)."""
    kept = _unique(
        (
            spec
            for spec in specs
            if spec.name.strip()
            and spec.value.strip()
            and _spec_key(spec.name) not in _SPEC_LABEL_TO_FIELD
        ),
        key=lambda spec: _spec_key(spec.name),
    )
    return [
        SpecValue(name=spec.name.strip(), value=spec.value.strip())
        for spec in kept[:_MAX_SPEC_ROWS]
    ]


def extract_device_spec(
    document_text: str, source_document: str, llm: Optional[LLMOverride] = None
) -> DeviceSpec:
    """Full pipeline for one document: raw extraction, then deterministic
    IP/LED normalization into a clean `DeviceSpec`."""
    extraction, source, warnings = _extract_document(
        document_text, source_document, llm
    )
    spec = _normalize_to_device_spec(extraction)
    spec.extraction_source = source
    spec.extraction_warnings = warnings
    return spec


def extract_device_specs(
    text_a: str,
    name_a: str,
    text_b: str,
    name_b: str,
    llm: Optional[LLMOverride] = None,
) -> tuple[DeviceSpec, DeviceSpec]:
    """Run both extractions concurrently; the LLM calls are I/O-bound."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(extract_device_spec, text_a, name_a, llm)
        future_b = pool.submit(extract_device_spec, text_b, name_b, llm)
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
        "document_type": {
            "device_a": device_a.document_type,
            "device_b": device_b.document_type,
        },
        "extra_specs": _merge_spec_rows(device_a, device_b),
    }


def _merge_spec_rows(device_a: DeviceSpec, device_b: DeviceSpec) -> list[dict]:
    """One row per open-ended spec name (matched case-insensitively), A's
    order first; a side that doesn't list a spec gets None."""
    rows: dict[str, dict] = {}
    for side, spec in (("device_a", device_a), ("device_b", device_b)):
        for item in spec.specs:
            row = rows.setdefault(
                _spec_key(item.name),
                {"name": item.name, "device_a": None, "device_b": None},
            )
            if row[side] is None:
                row[side] = item.value
    return list(rows.values())[:_MAX_SPEC_ROWS]


def _is_install_document(spec: DeviceSpec) -> bool:
    """Quick start guides (and records from before document types) describe an
    installation, so their install effort can be scored and compared."""
    return spec.document_type in (None, DocumentType.QUICK_START_GUIDE.value)


def _build_key_risks(spec: DeviceSpec) -> list[str]:
    risks = []
    # With no documented provisioning, the primary method is only the
    # conservative Console fallback; for a datasheet that would invent a risk.
    provisioning_known = _is_install_document(spec) or bool(spec.provisioning_methods)
    if provisioning_known and spec.provisioning_method == ProvisioningMethod.CONSOLE:
        risks.append(
            "Requires a technician with a laptop and terminal emulator on-site "
            "for initial console setup."
        )
    if (
        provisioning_known
        and spec.default_ip
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


def _strengths_phrase(advantages: Optional[list[str]]) -> str:
    """Up to three real advantages as one phrase, without the "Unique
    capability:" prefix; empty when only the no-advantages message is there."""
    real = [a for a in advantages or [] if a != NO_ADVANTAGES_MESSAGE][:3]
    return "; ".join(a.removeprefix("Unique capability: ").rstrip(".") for a in real)


def _build_spec_talk_track(
    device_a: DeviceSpec,
    device_b: DeviceSpec,
    perspective: Optional[Perspective],
    advantages_a: list[str],
    advantages_b: list[str],
) -> str:
    """Talk track when install effort can't be compared because the documents
    aren't both quick start guides; built only from the spec advantages."""
    a, b = device_a.model_name, device_b.model_name
    intro = (
        "Install effort isn't compared because these documents don't both "
        "describe an installation procedure."
    )
    strengths_a = _strengths_phrase(advantages_a)
    strengths_b = _strengths_phrase(advantages_b)
    if perspective == "sales":
        if strengths_a:
            text = f"Lead with what {a} offers that {b} doesn't: {strengths_a}."
        else:
            text = (
                f"No spec advantages for {a} were identified from the documents, "
                f"so differentiate on factors outside them."
            )
        if strengths_b:
            text += f" Be ready for {b} to point to: {strengths_b}."
        return f"{intro} {text}"
    parts = [intro]
    if strengths_a:
        parts.append(f"{a} stands out for: {strengths_a}.")
    if strengths_b:
        parts.append(f"{b} stands out for: {strengths_b}.")
    if not (strengths_a or strengths_b):
        parts.append("Neither document shows a clear spec advantage over the other.")
    return " ".join(parts)


def _build_sales_talk_track(
    device_a: DeviceSpec,
    score_a: int,
    device_b: DeviceSpec,
    score_b: int,
    perspective: Optional[Perspective] = None,
    advantages_a: Optional[list[str]] = None,
) -> str:
    """Talk track for one comparison.

    In "sales" perspective device A is the user's own product, so the advice
    flips with who wins on install effort. Otherwise (neutral, or a record
    from before perspectives) it describes the comparison without a side.
    """
    if score_a == score_b:
        return (
            f"{device_a.model_name} and {device_b.model_name} are tied on "
            f"install effort (Installation Friction Index {score_a} each). "
            f"Differentiate on the capability and spec advantages above "
            f"rather than on field labor."
        )

    if perspective == "sales" and score_a > score_b:
        ours, theirs = device_a.model_name, device_b.model_name
        track = (
            f"{ours} takes more field effort to install than {theirs} "
            f"(Installation Friction Index {score_a} vs. {score_b}), so don't "
            f"compete on install speed."
        )
        strengths = _strengths_phrase(advantages_a)
        if strengths:
            track += (
                f" Lead with what {ours} offers that {theirs} doesn't: {strengths}."
            )
        else:
            track += (
                f" No spec advantages for {ours} were identified from the "
                f"documents, so differentiate on factors outside them."
            )
        return track + (
            f" Expect {theirs} to lead with easier installation, "
            f"and be ready to address it."
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
    if perspective == "sales":
        # Ours won: lead with the install advantage against the incumbent.
        return (
            f"{winner} installs with meaningfully less field labor than {loser} "
            f"(Installation Friction Index {w_score} vs. {l_score}). Lead with "
            f"faster time-to-online and lower truck-roll/support cost whenever "
            f"{loser} is the incumbent."
        )
    return (
        f"{winner} installs with meaningfully less field labor than {loser} "
        f"(Installation Friction Index {w_score} vs. {l_score}), which means "
        f"faster time-to-online and lower truck-roll and support cost."
    )


def synthesize_battlecard(
    device_a: DeviceSpec,
    device_b: DeviceSpec,
    perspective: Optional[Perspective] = None,
) -> BattlecardResponse:
    """Deterministically compare two normalized `DeviceSpec` objects into a
    `BattlecardResponse`. Rule-based rather than LLM-routed, since free-form
    prose doesn't map onto these typed fields.

    Install friction is scored only when both documents are quick start
    guides; otherwise both scores are None and the talk track is spec-based."""
    advantages_a, advantages_b = _build_key_advantages(device_a, device_b)
    score_a: Optional[int] = None
    score_b: Optional[int] = None
    if _is_install_document(device_a) and _is_install_document(device_b):
        score_a = _compute_friction_score(device_a)
        score_b = _compute_friction_score(device_b)
        talk_track = _build_sales_talk_track(
            device_a, score_a, device_b, score_b, perspective, advantages_a
        )
    else:
        talk_track = _build_spec_talk_track(
            device_a, device_b, perspective, advantages_a, advantages_b
        )
    return BattlecardResponse(
        comparison_matrix=_build_comparison_matrix(device_a, device_b),
        installation_friction_score_device_a=score_a,
        installation_friction_score_device_b=score_b,
        key_risks_device_a=_build_key_risks(device_a),
        key_risks_device_b=_build_key_risks(device_b),
        key_advantages_device_a=advantages_a,
        key_advantages_device_b=advantages_b,
        sales_talk_track=talk_track,
        extraction_source_device_a=device_a.extraction_source,
        extraction_source_device_b=device_b.extraction_source,
        perspective=perspective,
        warnings=[
            f"{spec.model_name}: {warning}"
            for spec in (device_a, device_b)
            for warning in spec.extraction_warnings
        ],
    )
