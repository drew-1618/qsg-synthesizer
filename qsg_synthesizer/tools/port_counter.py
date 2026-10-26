"""Deterministic port-range parsing for physical port descriptions.

Port names loosely follow "LAN1-LAN4" (or "LAN 1~4") range notation for a bank of
identical ports, or a single name like "PON"/"Console" for one port.
Expanding a range into a label + count is a fixed regex over text
qsg_extractor already extracted, never an LLM guess.
"""

from __future__ import annotations

import re
from typing import Tuple

__all__ = ["expand_port_range"]

_RANGE_RE = re.compile(r"^([A-Za-z]+)\s*(\d+)\s*(?:[-–—~]|\bto\b)\s*[A-Za-z]*\s*(\d+)$")


def expand_port_range(port_name: str) -> Tuple[str, int]:
    """Expand "LAN1-LAN4"-style range notation into (label, count), e.g.
    ("LAN", 4). A port name with no range notation returns (port_name, 1)
    unchanged."""
    match = _RANGE_RE.match(port_name.strip())
    if not match:
        return port_name, 1
    prefix, start, end = match.group(1), int(match.group(2)), int(match.group(3))
    return prefix, max(end - start + 1, 1)
