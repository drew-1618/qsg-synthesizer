"""Deterministic comparison helpers for capability/spec fields.

Ranking Wi-Fi generations and parsing throughput strings from free text are
both handled here with fixed lookup tables and regexes rather than LLM
judgement, per CLAUDE.md's "use deterministic tools for normalization"
principle.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["rank_wifi_standard", "parse_speed_mbps"]

# Higher = newer/faster. 802.11a and 802.11b are roughly contemporaries, so
# they share a rank.
_WIFI_GENERATION_RANK = {
    "a": 1,
    "b": 1,
    "g": 2,
    "n": 3,
    "ac": 4,
    "ax": 5,
    "be": 6,
}
_WIFI_LETTERS_RE = re.compile(r"802\.11\s?([a-z]{1,2})\b", re.IGNORECASE)
_WIFI_GENERATION_RE = re.compile(r"wi-?fi\s?(\d)e?\b", re.IGNORECASE)
_WIFI_GENERATION_TO_LETTERS = {"4": "n", "5": "ac", "6": "ax", "7": "be"}

_SPEED_RE = re.compile(r"([\d.]+)\s*(gbps|gbit|mbps|mbit|gb|mb)\b", re.IGNORECASE)
_SPEED_UNIT_MULTIPLIER_MBPS = {"g": 1000.0, "m": 1.0}


def rank_wifi_standard(standard: Optional[str]) -> Optional[int]:
    """Relative newness rank for a Wi-Fi standard string (higher = newer),
    or None if no recognizable "802.11<letters>" or "Wi-Fi <generation>"
    token is present."""
    if not standard:
        return None
    letters_match = _WIFI_LETTERS_RE.search(standard)
    if letters_match:
        return _WIFI_GENERATION_RANK.get(letters_match.group(1).lower())
    generation_match = _WIFI_GENERATION_RE.search(standard)
    if generation_match:
        letters = _WIFI_GENERATION_TO_LETTERS.get(generation_match.group(1))
        if letters:
            return _WIFI_GENERATION_RANK.get(letters)
    return None


def parse_speed_mbps(text: Optional[str]) -> Optional[float]:
    """Extract the first throughput figure from free text, normalized to
    Mbps, or None if no recognizable "<number> <G/Mbps>" token is present."""
    if not text:
        return None
    match = _SPEED_RE.search(text)
    if not match:
        return None
    value, unit = match.groups()
    multiplier = _SPEED_UNIT_MULTIPLIER_MBPS[unit[0].lower()]
    return float(value) * multiplier
