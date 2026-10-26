"""Schemas shared between the orchestrator and the extraction subagent.

`QSGExtraction` is the contract the `qsg_extractor` subagent must produce:
raw, unvalidated text pulled from a single document. Numeric/IP/LED
normalization happens afterward via the deterministic tools in
`qsg_synthesizer/tools/`, never inside the extraction step itself.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class RawProvisioningMethod(str, Enum):
    TR069 = "TR-069"
    DHCP = "DHCP"
    WEB_GUI = "Web GUI"
    CONSOLE = "Console"
    QR_MOBILE_APP = "QR/Mobile App"
    OTHER = "Other"


class SetupDifficulty(str, Enum):
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"


class DocumentType(str, Enum):
    QUICK_START_GUIDE = "quick_start_guide"
    DATASHEET = "datasheet"
    OTHER = "other"


class SpecItem(BaseModel):
    """One open-ended specification, copied verbatim (e.g. "Power draw": "12 W")."""

    name: str
    value: str


class CablingPort(BaseModel):
    port_name: str
    port_type: str
    notes: Optional[str] = None


class LEDIndicator(BaseModel):
    led_name: str
    raw_description: str


class QSGExtraction(BaseModel):
    device_name: str
    source_document: str
    default_ip: Optional[str] = None
    provisioning_methods: List[RawProvisioningMethod] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    cabling_ports: List[CablingPort] = Field(default_factory=list)
    led_indicators: List[LEDIndicator] = Field(default_factory=list)
    wifi_standard: Optional[str] = None
    wan_speed: Optional[str] = None
    form_factor: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    # The model's assessment of the setup experience. Optional: heuristic
    # extraction can't judge these (it only counts numbered steps), and a
    # malformed assessment is dropped rather than discarding the rest of the
    # extraction.
    step_count: Optional[int] = Field(default=None, ge=0, le=200)
    setup_difficulty: Optional[SetupDifficulty] = None
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    # What kind of document this is, and any specs beyond the fixed fields
    # above. Optional for the same reason as the assessment.
    document_type: Optional[DocumentType] = None
    specs: List[SpecItem] = Field(default_factory=list)


ASSESSMENT_FIELDS = frozenset({"step_count", "setup_difficulty", "strengths", "gaps"})

# Fields whose malformed value is dropped rather than failing the extraction.
DROPPABLE_FIELDS = ASSESSMENT_FIELDS | frozenset({"document_type", "specs"})
