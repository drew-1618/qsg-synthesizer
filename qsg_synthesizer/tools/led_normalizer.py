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
from typing import Dict, Optional, Tuple

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

# What an LED means, for descriptions the color/pattern table can't classify
# ("On: Ready", "Blinking: Upgrading firmware"). Checked in this order, so a
# fault word beats a nominal one ("Solid: Connection failed").
_NO_POWER_RE = re.compile(r"\bno power\b|\bpowered off\b", re.IGNORECASE)
_FAULT_WORDS_RE = re.compile(
    r"\b(?:fail\w*|errors?|fault\w*|problem|alarm|overheat\w*"
    r"|no (?:\w+ )?(?:signal|link|connection|internet|service|sync)"
    r"|not (?:connected|registered|detected|available)|disconnected|offline"
    r"|loss of)\b",
    re.IGNORECASE,
)
_BOOTING_WORDS_RE = re.compile(
    r"\b(?:boot\w*|starting|start-?up|upgrad\w*|updat\w*|connecting|registering|"
    r"initiali[sz]\w*|negotiat\w*|ranging|scanning|searching|pairing|"
    r"in progress|activity|transmitting|traffic)\b",
    re.IGNORECASE,
)
_ONLINE_WORDS_RE = re.compile(
    r"\b(?:connected|ready|normal|online|link (?:is )?up|operational|working|"
    r"registered|synchroni[sz]ed|powered(?: on)?|established)\b",
    re.IGNORECASE,
)


class LEDState(str, Enum):
    BOOTING = "BOOTING"
    ONLINE = "ONLINE"
    FAULT = "FAULT"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"


# (normalized_color, normalized_pattern) -> LEDState.
# Convention followed across telecom QSGs: solid green (or white/blue on
# newer CPE) is nominal, blinking/pulsing signals init/boot activity, and red
# (any pattern) or solid amber signals a fault condition.
_STATE_TABLE: Dict[Tuple[Optional[str], Optional[str]], LEDState] = {
    ("green", "solid"): LEDState.ONLINE,
    ("green", "blinking"): LEDState.BOOTING,
    ("green", "pulsing"): LEDState.BOOTING,
    ("amber", "blinking"): LEDState.BOOTING,
    ("amber", "pulsing"): LEDState.BOOTING,
    ("amber", "solid"): LEDState.FAULT,
    ("white", "solid"): LEDState.ONLINE,
    ("blue", "solid"): LEDState.ONLINE,
    ("white", "blinking"): LEDState.BOOTING,
    ("white", "pulsing"): LEDState.BOOTING,
    ("blue", "blinking"): LEDState.BOOTING,
    ("blue", "pulsing"): LEDState.BOOTING,
    ("red", "solid"): LEDState.FAULT,
    ("red", "blinking"): LEDState.FAULT,
    ("red", "pulsing"): LEDState.FAULT,
}


# Display order for a device's LED list: nominal state first, then
# in-progress, then fault, with anything unclassified pushed to the end.
_DISPLAY_ORDER = {
    LEDState.ONLINE: 0,
    LEDState.BOOTING: 1,
    LEDState.FAULT: 2,
    LEDState.OFF: 3,
    LEDState.UNKNOWN: 4,
}


def led_sort_key(state: LEDState) -> int:
    return _DISPLAY_ORDER[state]


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
        state = LEDState.OFF
    else:
        state = _STATE_TABLE.get((color, pattern)) or _state_from_meaning(
            description, pattern
        )

    return NormalizedLED(
        raw=description,
        color=color,
        pattern=pattern,
        frequency_hz=frequency_hz,
        state=state,
    )


def _state_from_meaning(description: str, pattern: Optional[str]) -> LEDState:
    """Classify an LED the color/pattern table missed (most often one described
    without a color) from what the description says it means, then from its
    pattern alone: lit steadily is nominal, blinking is activity."""
    if _NO_POWER_RE.search(description):
        return LEDState.OFF
    if _FAULT_WORDS_RE.search(description):
        return LEDState.FAULT
    if _BOOTING_WORDS_RE.search(description):
        return LEDState.BOOTING
    if _ONLINE_WORDS_RE.search(description):
        return LEDState.ONLINE
    if pattern in ("blinking", "pulsing"):
        return LEDState.BOOTING
    if pattern == "solid":
        return LEDState.ONLINE
    return LEDState.UNKNOWN
