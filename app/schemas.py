"""API-facing schemas for the QSG Synthesizer service.

These are the normalized, validated shapes returned to callers. Contrast
with `app/models/schemas.py::QSGExtraction`, which is the raw, unvalidated
contract the extraction subagent fills in before the deterministic tools in
`app/tools/` clean it up into a `DeviceSpec`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.tools.led_normalizer import LEDState

__all__ = [
    "SpecValue",
    "Perspective",
    "LEDStatus",
    "ProvisioningMethod",
    "DeviceSpec",
    "BattlecardResponse",
    "CompareResponse",
    "HistorySummary",
    "HistoryRecord",
    "HistoryPage",
]


# "sales": side A is the user's own device and side B the competitor's, and the
# talk track is written from A's side. "neutral" (or None, for records saved
# before this existed) compares two devices without picking a side.
Perspective = Literal["sales", "neutral"]


class ProvisioningMethod(str, Enum):
    TR069 = "TR-069"
    WEB_GUI = "Web GUI"
    CONSOLE = "Console"
    DHCP = "DHCP"


class LEDStatus(BaseModel):
    # Which physical LED this describes (e.g. "Power"); None for records
    # saved before names were carried through.
    name: Optional[str] = None
    state: LEDState
    color: Optional[str] = None
    pattern: Optional[str] = None
    frequency_hz: Optional[float] = None
    description: str


class SpecValue(BaseModel):
    name: str
    value: str


class DeviceSpec(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    default_ip: Optional[str] = None
    is_private_ip: bool = False
    provisioning_method: ProvisioningMethod
    # Every method the document lists, as the raw extractor's labels
    # (e.g. "QR/Mobile App"); `provisioning_method` is the primary one
    # used for scoring.
    provisioning_methods: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    led_states: List[LEDStatus] = Field(default_factory=list)
    ports: List[str] = Field(default_factory=list)
    wifi_standard: Optional[str] = None
    wan_speed: Optional[str] = None
    form_factor: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    # LLM assessment of the setup experience; unset for heuristic extraction
    # and for records saved before these were tracked.
    step_count: Optional[int] = None
    setup_difficulty: Optional[str] = None
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    # "llm" or "heuristic"; set by the orchestrator, not the document
    extraction_source: Optional[str] = None
    # "quick_start_guide", "datasheet" or "other"; None when not determined
    # (treated as a quick start guide, as before document types existed).
    document_type: Optional[str] = None
    # Specs beyond the fixed fields above, e.g. power draw or certifications.
    specs: List[SpecValue] = Field(default_factory=list)
    # Notes about how the document was read, e.g. only part of a long one.
    extraction_warnings: List[str] = Field(default_factory=list)


class BattlecardResponse(BaseModel):
    comparison_matrix: dict
    # None when install effort isn't comparable (not both quick start guides).
    installation_friction_score_device_a: Optional[int] = Field(
        default=None, ge=1, le=10
    )
    installation_friction_score_device_b: Optional[int] = Field(
        default=None, ge=1, le=10
    )
    key_risks_device_a: List[str]
    key_risks_device_b: List[str]
    key_advantages_device_a: List[str]
    key_advantages_device_b: List[str]
    sales_talk_track: str
    # None on history records saved before provenance was tracked
    extraction_source_device_a: Optional[str] = None
    extraction_source_device_b: Optional[str] = None
    perspective: Optional[Perspective] = None
    warnings: List[str] = Field(default_factory=list)


class CompareResponse(BattlecardResponse):
    """A fresh comparison plus the ID of the history record it was saved as."""

    history_id: str


class HistorySummary(BaseModel):
    id: str
    created_at: datetime
    device_a_name: str
    device_b_name: str
    installation_friction_score_device_a: Optional[int] = None
    installation_friction_score_device_b: Optional[int] = None
    perspective: Optional[Perspective] = None
    # Hash of both input documents; None on records saved before it was tracked.
    input_fingerprint: Optional[str] = None


class SourceDocument(BaseModel):
    """A comparison input as the pipeline read it; the text itself is stored
    once per distinct content, under its hash (see history.py)."""

    name: str
    text_sha256: str


class HistoryRecord(BaseModel):
    id: str
    created_at: datetime
    device_a_name: str
    device_b_name: str
    battlecard: BattlecardResponse
    input_fingerprint: Optional[str] = None
    # [device A, device B]; empty on records saved before sources were kept.
    sources: List[SourceDocument] = Field(default_factory=list)


class HistoryStack(BaseModel):
    """Comparisons of the same device pair in the same mode, newest first."""

    key: str
    latest: HistorySummary
    others: List[HistorySummary] = Field(default_factory=list)
    # Older entries made from the same documents as a newer entry in the stack.
    redundant_ids: List[str] = Field(default_factory=list)


class HistoryPage(BaseModel):
    items: List[HistorySummary]
    total: int
    limit: int
    offset: int


class HistoryStackPage(BaseModel):
    items: List[HistoryStack]
    # Stacks matching the query; `record_total` counts the records in them.
    total: int
    record_total: int
    limit: int
    offset: int
