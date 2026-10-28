"""Per-field scores for one extraction against the expected reading in
`evals/expected.json`.

Every score is between 0 and 1. Scalars score 1 or 0. Lists score F1: a
read item counts as correct when it contains one of the expected keywords,
and an expected keyword counts as found when any read item contains it
(both ignoring case and punctuation). Spec names score recall only, since a
datasheet lists more specs than are worth writing down as expected.
"""

from __future__ import annotations

import re
from typing import Optional

from qsg_synthesizer.models.schemas import QSGExtraction
from qsg_synthesizer.tools.capability_compare import (
    parse_speed_mbps,
    rank_wifi_standard,
)

# Scored in this order in reports.
FIELDS = (
    "device_name",
    "document_type",
    "default_ip",
    "provisioning_methods",
    "required_tools",
    "ports",
    "leds",
    "wifi_standard",
    "wan_speed",
    "specs",
)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _contains(text: str, keyword: str) -> bool:
    return _key(keyword) in _key(text)


def keyword_f1(read: list[str], expected: list[str]) -> float:
    """F1 of read items against expected keywords; 1 when both are empty."""
    if not read and not expected:
        return 1.0
    if not read or not expected:
        return 0.0
    precision = sum(any(_contains(r, k) for k in expected) for r in read) / len(read)
    recall = sum(any(_contains(r, k) for r in read) for k in expected) / len(expected)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def keyword_recall(read: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0
    return sum(any(_contains(r, k) for r in read) for k in expected) / len(expected)


def set_f1(read: list[str], expected: list[str]) -> float:
    """F1 for exact labels, such as provisioning methods."""
    read_set, expected_set = set(read), set(expected)
    if not read_set and not expected_set:
        return 1.0
    hits = len(read_set & expected_set)
    if hits == 0:
        return 0.0
    precision, recall = hits / len(read_set), hits / len(expected_set)
    return 2 * precision * recall / (precision + recall)


def _same(read: Optional[object], expected: Optional[object]) -> float:
    return 1.0 if read == expected else 0.0


def score_extraction(extraction: QSGExtraction, expected: dict) -> dict[str, float]:
    """Scores for each field `expected` lists."""
    scores: dict[str, float] = {}
    if "device_name" in expected:
        scores["device_name"] = float(
            _contains(extraction.device_name, expected["device_name"])
        )
    if "document_type" in expected:
        read_type = extraction.document_type.value if extraction.document_type else None
        scores["document_type"] = _same(read_type, expected["document_type"])
    if "default_ip" in expected:
        scores["default_ip"] = _same(extraction.default_ip, expected["default_ip"])
    if "provisioning_methods" in expected:
        scores["provisioning_methods"] = set_f1(
            [m.value for m in extraction.provisioning_methods],
            expected["provisioning_methods"],
        )
    if "required_tools" in expected:
        scores["required_tools"] = keyword_f1(
            extraction.required_tools, expected["required_tools"]
        )
    if "ports" in expected:
        scores["ports"] = keyword_f1(
            [p.port_name for p in extraction.cabling_ports], expected["ports"]
        )
    if "leds" in expected:
        # One entry per LED, however many states it has.
        names = list(dict.fromkeys(led.led_name for led in extraction.led_indicators))
        scores["leds"] = keyword_f1(names, expected["leds"])
    if "wifi_standard" in expected:
        scores["wifi_standard"] = _same(
            rank_wifi_standard(extraction.wifi_standard),
            rank_wifi_standard(expected["wifi_standard"]),
        )
    if "wan_speed" in expected:
        scores["wan_speed"] = _same(
            parse_speed_mbps(extraction.wan_speed),
            parse_speed_mbps(expected["wan_speed"]),
        )
    if "specs" in expected:
        scores["specs"] = keyword_recall(
            [spec.name for spec in extraction.specs], expected["specs"]
        )
    return scores
