"""Deterministic comparison helpers for capability/spec fields.

Ranking Wi-Fi generations and parsing throughput strings from free text are
both handled here with fixed lookup tables and regexes rather than LLM
judgement, per CLAUDE.md's "use deterministic tools for normalization"
principle.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["rank_wifi_standard", "parse_speed_mbps", "capability_key"]

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
# One "802.11" prefix plus any slash/comma-joined suffixes, e.g. "802.11b/g/n/ac".
_WIFI_LETTERS_RE = re.compile(
    r"802\.11\s?([a-z]{1,2}\b(?:\s*[/,&]\s*(?:802\.11\s?)?[a-z]{1,2}\b)*)",
    re.IGNORECASE,
)
_WIFI_SUFFIX_RE = re.compile(r"[a-z]{1,2}", re.IGNORECASE)
_WIFI_GENERATION_RE = re.compile(r"wi-?fi\s?(\d)e?\b", re.IGNORECASE)
_WIFI_GENERATION_TO_LETTERS = {"4": "n", "5": "ac", "6": "ax", "7": "be"}

# Words that change a capability's phrasing but not which feature it names.
_CAPABILITY_FILLER = frozenset(
    {"a", "an", "and", "the", "for", "with", "of", "support", "supports", "supported"}
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WIFI_SPELLINGS_RE = re.compile(r"\bwi fi\b")

_SPEED_RE = re.compile(
    r"(\d+(?:\.\d+)?|\.\d+)\s*(gbps|gbit|mbps|mbit|gb|mb)\b", re.IGNORECASE
)
_SPEED_UNIT_MULTIPLIER_MBPS = {"g": 1000.0, "m": 1.0}


def rank_wifi_standard(standard: Optional[str]) -> Optional[int]:
    """Relative newness rank for a Wi-Fi standard string (higher = newer),
    or None if no recognizable "802.11<letters>" or "Wi-Fi <generation>"
    token is present. Multi-standard strings ("802.11b/g/n/ac") rank by the
    newest standard listed."""
    if not standard:
        return None
    ranks = []
    for chain in _WIFI_LETTERS_RE.findall(standard):
        for letters in _WIFI_SUFFIX_RE.findall(chain):
            rank = _WIFI_GENERATION_RANK.get(letters.lower())
            if rank is not None:
                ranks.append(rank)
    for generation in _WIFI_GENERATION_RE.findall(standard):
        letters = _WIFI_GENERATION_TO_LETTERS.get(generation)
        if letters:
            ranks.append(_WIFI_GENERATION_RANK[letters])
    return max(ranks) if ranks else None


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


def capability_key(capability: str) -> tuple[str, ...]:
    """Comparison key for a free-text capability, so the same feature worded
    differently by each document ("Mesh Wi-Fi support" / "Supports WiFi
    mesh") isn't reported as unique to both devices. Ignores case,
    punctuation, word order and filler words."""
    text = _NON_ALNUM_RE.sub(" ", capability.lower())
    text = _WIFI_SPELLINGS_RE.sub("wifi", text)
    words = {word for word in text.split() if word not in _CAPABILITY_FILLER}
    return tuple(sorted(words))
