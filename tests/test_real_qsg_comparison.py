"""Comparison-logic tests against two real, publicly published QSGs.

The fixtures in `tests/fixtures/` are unmodified NETGEAR Quick Start Guides
downloaded from downloads.netgear.com:

- `netgear_r7000_qsg.pdf`   - Nighthawk R7000 (AC1900, Wi-Fi 5)
- `netgear_raxe300_qsg.pdf` - Nighthawk RAXE300 (AXE7800, Wi-Fi 6E)

The PDFs go through the real `pdf_parser`. The model call is mocked: each
canned extraction below is what an extractor would plausibly return for the
guide, transcribed from its text (ports/LEDs from the "Overview" diagram,
the app + routerlogin.net install flow, the Armor bullet). Everything after
extraction (normalization, friction score, risks, advantages, talk track)
runs for real, and no test here can reach a live API.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ProvisioningMethod
from app.services import orchestrator
from app.services.orchestrator import (
    NO_RISKS_MESSAGE,
    extract_device_spec,
    synthesize_battlecard,
)
from app.tools.pdf_parser import extract_text_from_pdf

FIXTURES_DIR = Path(__file__).parent / "fixtures"
R7000_PDF = FIXTURES_DIR / "netgear_r7000_qsg.pdf"
RAXE300_PDF = FIXTURES_DIR / "netgear_raxe300_qsg.pdf"

_R7000_EXTRACTION = {
    "device_name": "NETGEAR Nighthawk R7000",
    "source_document": R7000_PDF.name,
    "default_ip": None,
    "provisioning_methods": ["QR/Mobile App", "Web GUI"],
    "required_tools": ["Mobile phone with the Nighthawk app"],
    "cabling_ports": [
        {"port_name": "Internet Port", "port_type": "Ethernet"},
        {"port_name": "LAN1-LAN4", "port_type": "Ethernet"},
        {"port_name": "USB 3.0 Port", "port_type": "USB"},
        {"port_name": "USB 2.0 Port", "port_type": "USB"},
    ],
    "led_indicators": [
        {"led_name": "Power LED", "raw_description": "Power LED"},
        {"led_name": "Internet LED", "raw_description": "Internet LED"},
        {"led_name": "2.4 GHz WiFi LED", "raw_description": "2.4 GHz WiFi LED"},
        {"led_name": "5 GHz WiFi LED", "raw_description": "5 GHz WiFi LED"},
    ],
    # Not in the QSG text itself; the model-line spec (AC1900) supplies it.
    "wifi_standard": "802.11ac",
    "capabilities": ["NETGEAR Armor", "USB 3.0 port", "USB 2.0 port"],
}

_RAXE300_EXTRACTION = {
    "device_name": "NETGEAR Nighthawk RAXE300",
    "source_document": RAXE300_PDF.name,
    "default_ip": None,
    "provisioning_methods": ["QR/Mobile App", "Web GUI"],
    "required_tools": ["Mobile phone with the Nighthawk app"],
    "cabling_ports": [
        {"port_name": "1G Internet Port", "port_type": "Ethernet"},
        {
            "port_name": "Multi-Gig Internet Port",
            "port_type": "Ethernet",
            "notes": "Doubles as Ethernet LAN port 5",
        },
        {"port_name": "LAN1-LAN4", "port_type": "Ethernet"},
        {"port_name": "USB 3.0 Port", "port_type": "USB"},
    ],
    "led_indicators": [
        {"led_name": "Power LED", "raw_description": "Power LED"},
        {"led_name": "Internet LED", "raw_description": "Internet LED"},
        {"led_name": "6 GHz WiFi LED", "raw_description": "6 GHz WiFi LED"},
    ],
    "wifi_standard": "802.11ax (Wi-Fi 6E)",
    "capabilities": ["NETGEAR Armor", "USB 3.0 port", "6 GHz Wi-Fi band"],
}


def _anthropic_reply(payload: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.input = payload
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    return response


@pytest.fixture
def mock_llm():
    """Patch Anthropic so each call returns the canned extraction whose
    device name appears in the document text it was sent."""
    client = MagicMock()

    def create(**kwargs):
        content = kwargs["messages"][0]["content"]
        # Only the RAXE300 guide mentions the 6 GHz WiFi LED.
        payload = _RAXE300_EXTRACTION if "6 GHz" in content else _R7000_EXTRACTION
        return _anthropic_reply(payload)

    client.messages.create.side_effect = create
    with (
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "LLM_PROVIDER": ""}),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        mock_anthropic.Anthropic.return_value = client
        yield client


@pytest.fixture(scope="module")
def r7000_text() -> str:
    return extract_text_from_pdf(R7000_PDF.read_bytes())


@pytest.fixture(scope="module")
def raxe300_text() -> str:
    return extract_text_from_pdf(RAXE300_PDF.read_bytes())


class TestRealPdfParsing:
    def test_r7000_text_has_expected_content(self, r7000_text):
        assert "Nighthawk app" in r7000_text
        assert "routerlogin.net" in r7000_text
        assert "USB 3.0" in r7000_text

    def test_raxe300_text_has_expected_content(self, raxe300_text):
        assert "Multi-Gig Internet port" in raxe300_text
        assert "6 GHz WiFi LED" in raxe300_text
        assert "routerlogin.net" in raxe300_text


class TestRealQsgComparison:
    def _specs(self, r7000_text, raxe300_text):
        return (
            extract_device_spec(r7000_text, R7000_PDF.name),
            extract_device_spec(raxe300_text, RAXE300_PDF.name),
        )

    def test_llm_receives_each_real_document_once(
        self, mock_llm, r7000_text, raxe300_text
    ):
        self._specs(r7000_text, raxe300_text)

        assert mock_llm.messages.create.call_count == 2
        sent = [
            c.kwargs["messages"][0]["content"]
            for c in mock_llm.messages.create.call_args_list
        ]
        assert any("Multi-Gig" in s for s in sent)
        assert any("USB 2.0" in s for s in sent)
        for call in mock_llm.messages.create.call_args_list:
            assert call.kwargs["tool_choice"]["type"] == "tool"

    def test_extraction_is_normalized_into_device_specs(
        self, mock_llm, r7000_text, raxe300_text
    ):
        r7000, raxe300 = self._specs(r7000_text, raxe300_text)

        assert r7000.model_name == "NETGEAR Nighthawk R7000"
        assert raxe300.model_name == "NETGEAR Nighthawk RAXE300"
        assert r7000.extraction_source == orchestrator.EXTRACTION_SOURCE_LLM
        # App-based install maps onto the API enum's closest analogue.
        assert r7000.provisioning_method == ProvisioningMethod.WEB_GUI
        assert raxe300.provisioning_method == ProvisioningMethod.WEB_GUI
        assert any(p.startswith("4x LAN") for p in r7000.ports)
        assert any("Multi-Gig" in p for p in raxe300.ports)

    def test_battlecard_matrix_and_scores(self, mock_llm, r7000_text, raxe300_text):
        card = synthesize_battlecard(*self._specs(r7000_text, raxe300_text))

        matrix = card.comparison_matrix
        assert matrix["model_name"]["device_a"].endswith("R7000")
        assert matrix["model_name"]["device_b"].endswith("RAXE300")
        assert matrix["wifi_standard"]["device_a"] == "802.11ac"
        assert matrix["wifi_standard"]["device_b"] == "802.11ax (Wi-Fi 6E)"
        # Neither guide uses a remote-provisioning method and both need one
        # tool: 1 base + 1 tool + 3 non-remote = 5 for each.
        assert card.installation_friction_score_device_a == 5
        assert card.installation_friction_score_device_b == 5
        assert "tied" in card.sales_talk_track
        assert card.extraction_source_device_a == orchestrator.EXTRACTION_SOURCE_LLM

    def test_battlecard_advantages_favor_newer_wifi_and_unique_features(
        self, mock_llm, r7000_text, raxe300_text
    ):
        card = synthesize_battlecard(*self._specs(r7000_text, raxe300_text))

        assert card.key_advantages_device_a == ["Unique capability: USB 2.0 port"]
        assert card.key_advantages_device_b[0].startswith("Newer Wi-Fi standard")
        assert "Unique capability: 6 GHz Wi-Fi band" in card.key_advantages_device_b

    def test_unclassifiable_led_names_are_flagged_as_risk(
        self, mock_llm, r7000_text, raxe300_text
    ):
        # The QSGs only name the LEDs; they never describe their states.
        card = synthesize_battlecard(*self._specs(r7000_text, raxe300_text))

        assert any("LED" in r for r in card.key_risks_device_a)
        assert NO_RISKS_MESSAGE not in card.key_risks_device_a

    def test_swapping_order_swaps_sides(self, mock_llm, r7000_text, raxe300_text):
        r7000, raxe300 = self._specs(r7000_text, raxe300_text)
        card = synthesize_battlecard(raxe300, r7000)

        assert card.key_advantages_device_a[0].startswith("Newer Wi-Fi standard")
        assert card.key_advantages_device_b == ["Unique capability: USB 2.0 port"]


class TestRealQsgApi:
    def test_compare_pdfs_endpoint_with_mocked_llm(self, mock_llm):
        client = TestClient(app)
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": (
                    R7000_PDF.name,
                    R7000_PDF.read_bytes(),
                    "application/pdf",
                ),
                "device_b_pdf": (
                    RAXE300_PDF.name,
                    RAXE300_PDF.read_bytes(),
                    "application/pdf",
                ),
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["comparison_matrix"]["model_name"]["device_b"].endswith("RAXE300")
        assert body["extraction_source_device_a"] == "llm"
        assert mock_llm.messages.create.call_count == 2


class TestRealQsgHeuristicFallback:
    def test_api_failure_still_yields_a_battlecard(self, r7000_text, raxe300_text):
        """If the model call blows up, both real guides still compare."""
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("boom")
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k", "LLM_PROVIDER": ""}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value = client
            a = extract_device_spec(r7000_text, R7000_PDF.name)
            b = extract_device_spec(raxe300_text, RAXE300_PDF.name)

        card = synthesize_battlecard(a, b)
        assert a.extraction_source == orchestrator.EXTRACTION_SOURCE_HEURISTIC
        assert 1 <= card.installation_friction_score_device_a <= 10
        assert 1 <= card.installation_friction_score_device_b <= 10
