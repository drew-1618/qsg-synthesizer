"""Schemas shared between the orchestrator and the extraction subagent.

`QSGExtraction` is the contract the `qsg_extractor` subagent must produce:
raw, unvalidated text pulled from a single document. Numeric/IP/LED
normalization happens afterward via the deterministic tools in
`app/tools/`, never inside the extraction step itself.
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
