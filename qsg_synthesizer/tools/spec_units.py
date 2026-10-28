"""Deterministic matching and unit alignment for open-ended spec rows.

Two datasheets rarely word or measure a spec the same way: one lists "Max
PHY rate: 18.7 Gbps", the other "Wireless throughput: 3000 Mbps"; one gives
weight in kg, the other in lb. This module maps spec names onto a shared
name and group, and re-expresses a value in the other side's unit when the
two differ, so the comparison table lines them up without an LLM guessing
at conversions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Display order of the groups in the comparison table and exports.
SPEC_GROUPS = (
    "Performance",
    "Wireless",
    "Interfaces",
    "Power",
    "Physical",
    "Environmental",
    "Compliance",
    "Other",
)

# Unit families a value can be converted within.
_RATE = "rate"
_POWER = "power"
_LENGTH = "length"
_WEIGHT = "weight"
_TEMPERATURE = "temperature"


@dataclass(frozen=True)
class SpecKind:
    """What a spec row is: a shared name (None when the name isn't one this
    module knows), its group, and the unit family its value is measured in."""

    canonical: Optional[str]
    group: str
    family: Optional[str] = None


# (name pattern, shared name, group, unit family), matched in order against
# the lowercased, whitespace-collapsed spec name.
_MAX = r"(?:max(?:imum)?\.? |peak |typical |total )?"
_KINDS: list[tuple[re.Pattern[str], str, str, Optional[str]]] = [
    (re.compile(p), name, group, family)
    for p, name, group, family in (
        (
            _MAX + r"(?:(?:aggregate |combined )?(?:wireless|wi-?fi|radio) "
            r"(?:throughput|speed|data rate|phy rate|rate|bandwidth)|phy rate)",
            "Max wireless rate",
            "Performance",
            _RATE,
        ),
        (
            _MAX + r"(?:routing|nat|wan|firewall|ip forwarding|l3) throughput",
            "Routing throughput",
            "Performance",
            _RATE,
        ),
        (_MAX + r"(?:aggregate )?throughput", "Max throughput", "Performance", _RATE),
        (
            r"switching (?:capacity|bandwidth|fabric)|backplane(?: bandwidth)?"
            r"|fabric capacity",
            "Switching capacity",
            "Performance",
            _RATE,
        ),
        (
            r"(?:packet )?forwarding (?:rate|capacity)|packet forwarding",
            "Forwarding rate",
            "Performance",
            None,
        ),
        (
            _MAX + r"(?:concurrent |simultaneous )?(?:clients|users|client devices)"
            r"(?: supported)?",
            "Max clients",
            "Performance",
            None,
        ),
        (
            r"(?:frequency |wi-?fi |radio )?bands?|radios?|frequenc(?:y|ies)",
            "Bands",
            "Wireless",
            None,
        ),
        (
            r"(?:spatial )?streams|mimo(?: configuration)?"
            r"|antennas?(?: configuration)?",
            "Spatial streams",
            "Wireless",
            None,
        ),
        (r"wan(?: ports?| interfaces?)?", "WAN", "Interfaces", None),
        (r"lan(?: ports?)?|ethernet(?: ports)?", "LAN", "Interfaces", None),
        (r"usb(?: ports?)?", "USB", "Interfaces", None),
        (r"uplinks?(?: ports?)?", "Uplinks", "Interfaces", None),
        (r"(?:voice|phone|fxs|pots)(?: ports?)?", "Voice ports", "Interfaces", None),
        (
            r"(?:downlink |access )?ports|interfaces|port configuration",
            "Ports",
            "Interfaces",
            None,
        ),
        (
            r"(?:total )?poe\+?\+? (?:power )?(?:budget|capacity)|(?:total )?poe budget"
            r"|power budget",
            "PoE budget",
            "Power",
            _POWER,
        ),
        (
            _MAX + r"power (?:consumption|draw|usage)|power",
            "Power consumption",
            "Power",
            _POWER,
        ),
        (
            r"power (?:input|supply|adapter|source|requirements?)"
            r"|input (?:power|voltage)|dc input",
            "Power input",
            "Power",
            None,
        ),
        (r"battery(?: backup)?", "Battery backup", "Power", None),
        (
            r"(?:physical |product |unit )?(?:dimensions?|size)(?: \(.*\))?",
            "Dimensions",
            "Physical",
            _LENGTH,
        ),
        (r"(?:net |unit |product )?weight(?: \(.*\))?", "Weight", "Physical", _WEIGHT),
        (r"mount(?:ing)?(?: options)?", "Mounting", "Physical", None),
        (r"enclosure|housing|chassis", "Enclosure", "Physical", None),
        (
            r"(?:operating )?temp(?:erature)?(?: range)?(?: \(operating\))?"
            r"|operating (?:temp(?:erature)?|environment)(?: range)?",
            "Operating temperature",
            "Environmental",
            _TEMPERATURE,
        ),
        (
            r"storage temp(?:erature)?(?: range)?",
            "Storage temperature",
            "Environmental",
            _TEMPERATURE,
        ),
        (
            r"(?:operating )?(?:relative )?humidity(?: range)?",
            "Humidity",
            "Environmental",
            None,
        ),
        (
            r"certifications?|regulatory(?: compliance)?|compliance|approvals"
            r"|safety(?: and emissions)?|emissions",
            "Certifications",
            "Compliance",
            None,
        ),
    )
]

# A "Power" row given in volts/amps (no watts) is the supply, not the draw.
_WATTS_RE = re.compile(r"\d\s*[mk]?w\b", re.IGNORECASE)

_NUMBER = r"-?\d+(?:[.,]\d+)?"
_UNITS: dict[str, dict[str, float]] = {
    # Factor to the family's base unit (bps, W, mm, kg).
    _RATE: {"bps": 1, "kbps": 1e3, "mbps": 1e6, "gbps": 1e9, "tbps": 1e12},
    _POWER: {"mw": 1e-3, "w": 1, "kw": 1e3},
    _LENGTH: {"mm": 1, "cm": 10, "in": 25.4, "inch": 25.4, "inches": 25.4},
    _WEIGHT: {"g": 1e-3, "kg": 1, "lb": 0.45359237, "lbs": 0.45359237, "oz": 0.0283495},
}
# How each unit is written back out.
_UNIT_LABELS = {
    "bps": "bps",
    "kbps": "Kbps",
    "mbps": "Mbps",
    "gbps": "Gbps",
    "tbps": "Tbps",
    "mw": "mW",
    "w": "W",
    "kw": "kW",
    "mm": "mm",
    "cm": "cm",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "g": "g",
    "kg": "kg",
    "lb": "lb",
    "lbs": "lb",
    "oz": "oz",
}
_RATE_UNIT_RE = re.compile(
    rf"({_NUMBER})\s*([kmgt]?)(?:bps|b/s|bit/s)\b", re.IGNORECASE
)
_POWER_UNIT_RE = re.compile(rf"({_NUMBER})\s*([mk]?w)\b", re.IGNORECASE)
_LENGTH_UNIT_RE = re.compile(r"\b(mm|cm|inches|inch|in)\b|(\")", re.IGNORECASE)
_WEIGHT_UNIT_RE = re.compile(rf"({_NUMBER})\s*(kg|lbs?|oz|g)\b", re.IGNORECASE)
_TEMPERATURE_RE = re.compile(
    rf"({_NUMBER})\s*°?\s*([cf])?\b(?:\s*(?:to|-|–|~)\s*({_NUMBER})\s*°?\s*([cf])\b)?",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(_NUMBER)


def classify_spec(name: str, value: str = "") -> SpecKind:
    """The shared name, group and unit family for a spec row."""
    key = " ".join(name.lower().rstrip(":.").split())
    for pattern, canonical, group, family in _KINDS:
        if pattern.fullmatch(key):
            if canonical == "Power consumption" and key == "power":
                if not _WATTS_RE.search(value):
                    return SpecKind("Power input", "Power")
            return SpecKind(canonical, group, family)
    return SpecKind(None, "Other")


@dataclass(frozen=True)
class _Quantity:
    unit: str  # the key in `_UNITS` (or "c"/"f" for temperature)
    values: tuple[float, ...]  # as written, in `unit`


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _parse(family: str, value: str) -> Optional[_Quantity]:
    if family == _RATE:
        rate = _RATE_UNIT_RE.search(value)
        if not rate:
            return None
        return _Quantity(f"{rate.group(2).lower()}bps", (_number(rate.group(1)),))
    if family == _POWER:
        # "28 W typical, 36 W max": every figure, compared by the largest.
        found = _POWER_UNIT_RE.findall(value)
        units = {unit.lower() for _, unit in found}
        if len(units) != 1:
            return None
        return _Quantity(units.pop(), tuple(_number(n) for n, _ in found))
    if family == _LENGTH:
        unit_match = _LENGTH_UNIT_RE.search(value)
        if not unit_match:
            return None
        # 9.4" x 9.4" x 1.9" marks every figure; "240 x 240 x 48 mm" only the last.
        end = value.rindex('"') if unit_match.group(2) else unit_match.start()
        numbers = _NUMBER_RE.findall(value[:end])
        if not numbers:
            return None
        unit = (unit_match.group(1) or "in").lower()
        return _Quantity(unit, tuple(_number(n) for n in numbers))
    if family == _WEIGHT:
        weight = _WEIGHT_UNIT_RE.search(value)
        if not weight:
            return None
        return _Quantity(weight.group(2).lower(), (_number(weight.group(1)),))
    if family == _TEMPERATURE:
        temp = _TEMPERATURE_RE.search(value)
        if not temp or not (temp.group(2) or temp.group(4)):
            return None
        unit = (temp.group(4) or temp.group(2)).lower()
        values = [_number(temp.group(1))]
        if temp.group(3):
            values.append(_number(temp.group(3)))
        return _Quantity(unit, tuple(values))
    return None


def _convert(quantity: _Quantity, family: str, unit: str) -> tuple[float, ...]:
    if family == _TEMPERATURE:
        if quantity.unit == unit:
            return quantity.values
        if unit == "c":
            return tuple((v - 32) * 5 / 9 for v in quantity.values)
        return tuple(v * 9 / 5 + 32 for v in quantity.values)
    factors = _UNITS[family]
    return tuple(v * factors[quantity.unit] / factors[unit] for v in quantity.values)


def _format_number(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _display(values: tuple[float, ...], family: str, unit: str) -> str:
    if family == _TEMPERATURE:
        text = " to ".join(_format_number(v) for v in values)
        return f"{text} °{unit.upper()}"
    if family == _POWER and len(values) > 1:
        return f"{_format_number(max(values))} {_UNIT_LABELS[unit]} max"
    separator = " x " if family == _LENGTH else " to "
    text = separator.join(_format_number(v) for v in values)
    return f"{text} {_UNIT_LABELS[unit]}"


def convert_to_unit_of(
    family: Optional[str], reference: Optional[str], value: Optional[str]
) -> Optional[str]:
    """`value` re-expressed in `reference`'s unit ("9 lb" against "4.1 kg" is
    "4.08 kg"), or None when they share a unit or either can't be read."""
    if family is None or not reference or not value:
        return None
    target = _parse(family, reference)
    quantity = _parse(family, value)
    if target is None or quantity is None or target.unit == quantity.unit:
        return None
    return _display(_convert(quantity, family, target.unit), family, target.unit)
