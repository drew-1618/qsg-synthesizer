import json
import logging
from unittest.mock import MagicMock, patch

from app.services.orchestrator import (
    NO_ADVANTAGES_MESSAGE,
    NO_RISKS_MESSAGE,
    _build_key_risks,
    _heuristic_extract_qsg,
    extract_device_spec,
    extract_qsg_fields,
    synthesize_battlecard,
)
from app.tools.led_normalizer import LEDState

_FAKE_EXTRACTION = {
    "device_name": "NovaFiber GX400",
    "source_document": "device_a_qsg.md",
    "default_ip": None,
    "provisioning_methods": ["TR-069"],
    "required_tools": [],
    "cabling_ports": [],
    "led_indicators": [],
}


def _fake_anthropic_response(payload: dict) -> MagicMock:
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps(payload)
    response = MagicMock()
    response.content = [text_block]
    return response


class TestExtractViaAnthropic:
    def test_uses_anthropic_when_key_and_package_present(self):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _fake_anthropic_response(
                _FAKE_EXTRACTION
            )
            mock_anthropic.Anthropic.return_value = mock_client

            result = extract_qsg_fields("some document text", "device_a_qsg.md")

            assert result.device_name == "NovaFiber GX400"
            assert result.provisioning_methods == ["TR-069"]
            mock_client.messages.create.assert_called_once()
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs["model"] == "claude-haiku-4-5-20251001"
            assert "bounded document-extraction subagent" in kwargs["system"]
            assert "some document text" in kwargs["messages"][0]["content"]

    def test_falls_back_to_heuristic_on_api_error(self, caplog):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = RuntimeError("api unreachable")
            mock_anthropic.Anthropic.return_value = mock_client

            document_text = "# LegacyLink RG-210\n\nSome QSG content."
            with caplog.at_level(logging.WARNING, logger="app.services.orchestrator"):
                result = extract_qsg_fields(document_text, "device_b_qsg.md")

            assert result == _heuristic_extract_qsg(document_text, "device_b_qsg.md")
            assert "Anthropic extraction failed" in caplog.text
            assert "device_b_qsg.md" in caplog.text

    def test_falls_back_to_heuristic_without_api_key(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("ANTHROPIC_API_KEY", None)
            document_text = "# LegacyLink RG-210\n\nSome QSG content."
            result = extract_qsg_fields(document_text, "device_b_qsg.md")
            assert result == _heuristic_extract_qsg(document_text, "device_b_qsg.md")


_DEVICE_A_TEXT = """# Device A

## Key Features

- Wi-Fi 6 (802.11ax) tri-band with mesh support
- USB 3.0 port for external storage

## Specifications

- Wi-Fi Standard: 802.11ax (Wi-Fi 6), tri-band
- WAN Speed: Up to 2.5 Gbps (XGS-PON)
- Form Factor: Compact desktop, wall-mountable

## Cabling & Ports

| Port | Type | Notes |
|------|------|-------|
| LAN1-LAN4 | RJ45 Gigabit Ethernet | Auto-negotiating |
"""

_DEVICE_B_TEXT = """# Device B

## Key Features

- 2x FXS ports for analog phone service (VoIP)

## Specifications

- Wi-Fi Standard: 802.11ac (Wi-Fi 5), dual-band
- WAN Speed: Up to 100 Mbps (ADSL2+/VDSL2)
- Form Factor: Desktop, not wall-mountable

## Cabling & Ports

| Port | Type | Notes |
|------|------|-------|
| LAN1-LAN2 | RJ45 Fast Ethernet | Manual VLAN jumpers |
"""


class TestHeuristicCapabilityExtraction:
    def test_extracts_key_features_as_capabilities(self):
        extraction = _heuristic_extract_qsg(_DEVICE_A_TEXT, "a.md")
        assert extraction.capabilities == [
            "Wi-Fi 6 (802.11ax) tri-band with mesh support",
            "USB 3.0 port for external storage",
        ]

    def test_extracts_specifications_by_label(self):
        extraction = _heuristic_extract_qsg(_DEVICE_A_TEXT, "a.md")
        assert extraction.wifi_standard == "802.11ax (Wi-Fi 6), tri-band"
        assert extraction.wan_speed == "Up to 2.5 Gbps (XGS-PON)"
        assert extraction.form_factor == "Compact desktop, wall-mountable"

    def test_missing_specifications_section_leaves_fields_none(self):
        extraction = _heuristic_extract_qsg("# Device\n\nNo extra sections.", "x.md")
        assert extraction.wifi_standard is None
        assert extraction.wan_speed is None
        assert extraction.form_factor is None
        assert extraction.capabilities == []


class TestKeyAdvantages:
    def test_flags_newer_wifi_faster_wan_and_unique_capabilities(self):
        device_a = extract_device_spec(_DEVICE_A_TEXT, "a.md")
        device_b = extract_device_spec(_DEVICE_B_TEXT, "b.md")
        battlecard = synthesize_battlecard(device_a, device_b)

        advantages_a = " ".join(battlecard.key_advantages_device_a)
        advantages_b = " ".join(battlecard.key_advantages_device_b)

        assert "Newer Wi-Fi standard" in advantages_a
        assert "Faster WAN throughput" in advantages_a
        assert "Unique capability: USB 3.0 port for external storage" in advantages_a
        assert (
            "Unique capability: 2x FXS ports for analog phone service (VoIP)"
            in advantages_b
        )

    def test_falls_back_to_no_differentiation_message(self):
        device_a = extract_device_spec("# Device A\n\nNo extra sections.", "a.md")
        device_b = extract_device_spec("# Device B\n\nNo extra sections.", "b.md")
        battlecard = synthesize_battlecard(device_a, device_b)

        assert battlecard.key_advantages_device_a == [NO_ADVANTAGES_MESSAGE]
        assert battlecard.key_advantages_device_b == [NO_ADVANTAGES_MESSAGE]


class TestKeyRisksFallback:
    def test_falls_back_to_no_risks_message_when_nothing_flagged(self):
        device = extract_device_spec(
            "# Device\n\nAuto-provisions via TR-069. No console required.", "d.md"
        )
        assert _build_key_risks(device) == [NO_RISKS_MESSAGE]


_LED_ORDER_TEXT = """# Device

## LED Indicators

| LED | Meaning |
|-----|---------|
| Alert | solid red if optical signal is lost |
| Power | mysterious purple glow |
| Power | solid green once fully online |
| Power | blinking amber 2Hz while booting |
"""


class TestLedStateSorting:
    def test_orders_online_booting_fault_then_unknown(self):
        device = extract_device_spec(_LED_ORDER_TEXT, "device.md")
        assert [led.state for led in device.led_states] == [
            LEDState.ONLINE,
            LEDState.BOOTING,
            LEDState.FAULT,
            LEDState.UNKNOWN,
        ]

    def test_carries_pattern_and_frequency_for_display(self):
        device = extract_device_spec(_LED_ORDER_TEXT, "device.md")
        booting = next(
            led for led in device.led_states if led.state == LEDState.BOOTING
        )
        assert booting.pattern == "blinking"
        assert booting.frequency_hz == 2.0
