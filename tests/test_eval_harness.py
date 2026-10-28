"""The evaluation harness in evals/: its scoring, and the built-in parser's
scores over every document with a written-down expected reading, so a parser
change that reads them worse overall shows up here."""

import pytest

from evals import run_eval
from evals.scoring import keyword_f1, keyword_recall, score_extraction, set_f1
from qsg_synthesizer.models.schemas import (
    CablingPort,
    LEDIndicator,
    QSGExtraction,
    RawProvisioningMethod,
    SpecItem,
)
from qsg_synthesizer.services import orchestrator


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(orchestrator, "_select_llm_provider", lambda: None)


class TestScoring:
    def test_keywords_match_ignoring_case_and_punctuation(self):
        assert keyword_f1(["LAN1–LAN4", "PON port"], ["lan", "PON"]) == 1.0

    def test_extra_and_missing_items_lower_f1(self):
        # Precision 1/2 (Wi-Fi isn't a port), recall 1/2 (no DSL).
        assert keyword_f1(["LAN", "Wi-Fi"], ["LAN", "DSL"]) == pytest.approx(0.5)

    def test_empty_lists_agree(self):
        assert keyword_f1([], []) == 1.0
        assert keyword_f1(["screwdriver"], []) == 0.0
        assert set_f1([], []) == 1.0

    def test_spec_names_score_recall_only(self):
        assert keyword_recall(["Weight", "Color", "Fan"], ["weight"]) == 1.0
        assert keyword_recall(["Weight"], ["weight", "power"]) == 0.5

    def test_extraction_scores_each_listed_field(self):
        extraction = QSGExtraction(
            device_name="Acme Z1 Router",
            source_document="z.md",
            default_ip="192.168.1.1",
            provisioning_methods=[RawProvisioningMethod.WEB_GUI],
            cabling_ports=[CablingPort(port_name="WAN", port_type="RJ45")],
            led_indicators=[
                LEDIndicator(led_name="Power", raw_description="solid green"),
                LEDIndicator(led_name="Power", raw_description="off"),
            ],
            wifi_standard="Wi-Fi 6",
            wan_speed="Up to 1 Gbps",
            specs=[SpecItem(name="Weight", value="1 kg")],
        )
        scores = score_extraction(
            extraction,
            {
                "device_name": "Z1",
                "default_ip": "192.168.1.1",
                "provisioning_methods": ["Web GUI", "TR-069"],
                "ports": ["WAN"],
                "leds": ["Power"],
                "wifi_standard": "802.11ax",
                "wan_speed": "1000 Mbps",
                "specs": ["weight", "power"],
            },
        )
        assert scores == {
            "device_name": 1.0,
            "default_ip": 1.0,
            "provisioning_methods": pytest.approx(2 / 3),
            "ports": 1.0,
            "leds": 1.0,
            "wifi_standard": 1.0,
            "wan_speed": 1.0,
            "specs": 0.5,
        }


# The built-in parser's per-field means when this was written; raise them
# when the parser improves, never lower them to make a test pass.
HEURISTIC_FLOORS = {
    "device_name": 1.0,
    "document_type": 1.0,
    "default_ip": 1.0,
    "provisioning_methods": 0.85,
    "required_tools": 1.0,
    "ports": 0.86,
    "leds": 0.89,
    "wifi_standard": 1.0,
    "wan_speed": 0.92,
    "specs": 1.0,
}


def test_every_expected_document_exists():
    for document in run_eval.load_expected():
        assert (run_eval.ROOT / document).is_file(), document


def test_built_in_parser_meets_its_floors():
    results = run_eval.run("heuristic")
    assert len(results) == len(run_eval.load_expected())
    means = run_eval.field_means(results)
    below = {
        name: round(means[name], 3)
        for name, floor in HEURISTIC_FLOORS.items()
        if means[name] < floor - 0.005
    }
    assert below == {}


def test_report_lists_every_document_and_the_means():
    results = run_eval.run("heuristic", only="router")
    text = run_eval.report(results)
    assert "| router_qsg.md |" in text
    assert "| **Mean** |" in text
    assert "Read with: Built-in parser" in text


def test_an_unusable_provider_exits_without_calling_anything(monkeypatch, capsys):
    monkeypatch.setattr(orchestrator, "_provider_ready", lambda provider: False)
    monkeypatch.setattr(run_eval, "run", lambda *a: pytest.fail("ran"))
    assert run_eval.main(["--provider", "gemini", "--no-save"]) == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err
