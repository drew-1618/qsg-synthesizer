"""API-facing schemas for the QSG Synthesizer service.

These are the normalized, validated shapes returned to callers. Contrast
with `app/models/schemas.py::QSGExtraction`, which is the raw, unvalidated
contract the extraction subagent fills in before the deterministic tools in
`app/tools/` clean it up into a `DeviceSpec`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.tools.led_normalizer import LEDState

__all__ = [
    "LEDStatus",
    "ProvisioningMethod",
    "DeviceSpec",
    "BattlecardResponse",
    "HistorySummary",
    "HistoryRecord",
    "HistoryPage",
]


class ProvisioningMethod(str, Enum):
    TR069 = "TR-069"
    WEB_GUI = "Web GUI"
    CONSOLE = "Console"
    DHCP = "DHCP"


class LEDStatus(BaseModel):
    state: LEDState
    color: Optional[str] = None
    pattern: Optional[str] = None
    frequency_hz: Optional[float] = None
    description: str


class DeviceSpec(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    default_ip: Optional[str] = None
    is_private_ip: bool = False
    provisioning_method: ProvisioningMethod
    required_tools: List[str] = Field(default_factory=list)
    led_states: List[LEDStatus] = Field(default_factory=list)
    ports: List[str] = Field(default_factory=list)
    wifi_standard: Optional[str] = None
    wan_speed: Optional[str] = None
    form_factor: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)


class BattlecardResponse(BaseModel):
    comparison_matrix: dict
    installation_friction_score_device_a: int = Field(ge=1, le=10)
    installation_friction_score_device_b: int = Field(ge=1, le=10)
    key_risks_device_a: List[str]
    key_risks_device_b: List[str]
    key_advantages_device_a: List[str]
    key_advantages_device_b: List[str]
    sales_talk_track: str


class HistorySummary(BaseModel):
    id: str
    created_at: datetime
    device_a_name: str
    device_b_name: str
    installation_friction_score_device_a: int
    installation_friction_score_device_b: int


class HistoryRecord(BaseModel):
    id: str
    created_at: datetime
    device_a_name: str
    device_b_name: str
    battlecard: BattlecardResponse


class HistoryPage(BaseModel):
    items: List[HistorySummary]
    total: int
    limit: int
    offset: int
