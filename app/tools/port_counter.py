"""Deterministic counting of physical ports from extracted port-name strings.

Port names loosely follow "LAN1-LAN4" range notation for multi-port banks or
a single name like "PON"/"Console" for one port. Counting is a fixed regex
over text qsg_extractor already extracted, never an LLM guess.
"""

from __future__ import annotations

import re
from typing import List

__all__ = ["count_ports"]

# Matches "LAN1-LAN4", "LAN1-4", and "port 1 to 4" alike: a number, a
# separator (dash or "to"), an optional repeated non-numeric prefix (e.g.
# the repeated "LAN"), then the closing number.
_RANGE_RE = re.compile(r"(\d+)\s*(?:[-–—]|\bto\b)\s*[A-Za-z]*\s*(\d+)")


def count_ports(port_names: List[str]) -> int:
    """Sum the number of physical ports across a list of port descriptions,
    expanding "N-M" range notation (e.g. "LAN1-LAN4" -> 4) and counting
    anything else as a single port."""
    total = 0
    for name in port_names:
        match = _RANGE_RE.search(name)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            total += max(end - start + 1, 1)
        else:
            total += 1
    return total
