"""Deterministic LED description normalizer.

QSGs describe status LEDs in free text ("blinking amber 2Hz", "solid green").
This module regex-parses that text into a structured, comparable state so the
battlecard doesn't rely on an LLM re-interpreting the same phrase differently
across documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

_COLOR_ALIASES = {
    "green": "green",
    "amber": "amber",
    "yellow": "amber",
    "orange": "amber",
    "red": "red",
    "blue": "blue",
    "white": "white",
    "off": "off",
}

_PATTERN_ALIASES = {
    "solid": "solid",
    "steady": "solid",
    "on": "solid",
    "blinking": "blinking",
    "flashing": "blinking",
    "blink": "blinking",
    "flash": "blinking",
    "pulsing": "pulsing",
    "pulse": "pulsing",
    "breathing": "pulsing",
}

_COLOR_RE = re.compile(r"\b(" + "|".join(_COLOR_ALIASES) + r")\b", re.IGNORECASE)
_PATTERN_RE = re.compile(r"\b(" + "|".join(_PATTERN_ALIASES) + r")\b", re.IGNORECASE)
_FREQUENCY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*hz", re.IGNORECASE)


class LEDState(str, Enum):
    BOOTING = "BOOTING"
    ONLINE = "ONLINE"
    FAULT = "FAULT"
    UNKNOWN = "UNKNOWN"


# (normalized_color, normalized_pattern) -> LEDState.
# Convention followed across telecom QSGs: solid green is nominal, blinking/
# pulsing amber signals init/boot activity, and red (any pattern) or solid
# amber signals a fault condition.
_STATE_TABLE = {
    ("green", "solid"): LEDState.ONLINE,
    ("green", "blinking"): LEDState.BOOTING,
    ("green", "pulsing"): LEDState.BOOTING,
    ("amber", "blinking"): LEDState.BOOTING,
    ("amber", "pulsing"): LEDState.BOOTING,
    ("amber", "solid"): LEDState.FAULT,
    ("red", "solid"): LEDState.FAULT,
    ("red", "blinking"): LEDState.FAULT,
    ("red", "pulsing"): LEDState.FAULT,
}


@dataclass(frozen=True)
class NormalizedLED:
    raw: str
    color: Optional[str]
    pattern: Optional[str]
    frequency_hz: Optional[float]
    state: LEDState


def normalize_led(description: str) -> NormalizedLED:
    """Normalize a free-text LED description into a structured state."""
    color_match = _COLOR_RE.search(description)
    pattern_match = _PATTERN_RE.search(description)
    frequency_match = _FREQUENCY_RE.search(description)

    color = _COLOR_ALIASES[color_match.group(1).lower()] if color_match else None
    pattern = (
        _PATTERN_ALIASES[pattern_match.group(1).lower()] if pattern_match else None
    )
    frequency_hz = float(frequency_match.group(1)) if frequency_match else None

    if color == "off":
        state = LEDState.UNKNOWN
    else:
        state = _STATE_TABLE.get((color, pattern), LEDState.UNKNOWN)

    return NormalizedLED(
        raw=description,
        color=color,
        pattern=pattern,
        frequency_hz=frequency_hz,
        state=state,
    )
