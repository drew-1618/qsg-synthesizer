import json
import logging
from unittest.mock import MagicMock, patch

from app.services import orchestrator
from app.services.orchestrator import (
    NO_ADVANTAGES_MESSAGE,
    NO_RISKS_MESSAGE,
    _build_key_risks,
    _heuristic_extract_qsg,
    _select_llm_provider,
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
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = payload
    response = MagicMock()
    response.content = [tool_block]
    response.stop_reason = "tool_use"
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
            assert kwargs["model"] == "claude-haiku-5-5"
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
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("LLM_PROVIDER", None)
            document_text = "# LegacyLink RG-210\n\nSome QSG content."
            result = extract_qsg_fields(document_text, "device_b_qsg.md")
            assert result == _heuristic_extract_qsg(document_text, "device_b_qsg.md")


def _fake_gemini_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.text = json.dumps(payload)
    return response


class TestExtractViaGemini:
    def test_uses_gemini_when_key_and_package_present(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_API_KEY": "",
                    "GEMINI_API_KEY": "test-key",
                    "LLM_PROVIDER": "",
                },
            ),
            patch("app.services.orchestrator.genai") as mock_genai,
        ):
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = _fake_gemini_response(
                _FAKE_EXTRACTION
            )
            mock_genai.Client.return_value = mock_client

            result = extract_qsg_fields("some document text", "device_a_qsg.md")

            assert result.device_name == "NovaFiber GX400"
            assert result.provisioning_methods == ["TR-069"]
            mock_client.models.generate_content.assert_called_once()
            _, kwargs = mock_client.models.generate_content.call_args
            assert kwargs["model"] == "gemini-2.5-flash"
            assert "some document text" in kwargs["contents"]

    def test_falls_back_to_heuristic_on_api_error(self, caplog):
        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_API_KEY": "",
                    "GEMINI_API_KEY": "test-key",
                    "LLM_PROVIDER": "",
                },
            ),
            patch("app.services.orchestrator.genai") as mock_genai,
        ):
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = RuntimeError(
                "api unreachable"
            )
            mock_genai.Client.return_value = mock_client

            document_text = "# LegacyLink RG-210\n\nSome QSG content."
            with caplog.at_level(logging.WARNING, logger="app.services.orchestrator"):
                result = extract_qsg_fields(document_text, "device_b_qsg.md")

            assert result == _heuristic_extract_qsg(document_text, "device_b_qsg.md")
            assert "Gemini extraction failed" in caplog.text
            assert "device_b_qsg.md" in caplog.text


class TestSelectLlmProvider:
    def test_defaults_to_anthropic_when_both_keys_set(self):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g", "LLM_PROVIDER": ""},
        ):
            assert _select_llm_provider() == "anthropic"

    def test_uses_gemini_when_only_gemini_key_set(self):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "g", "LLM_PROVIDER": ""},
        ):
            assert _select_llm_provider() == "gemini"

    def test_override_forces_gemini_even_when_anthropic_key_present(self):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g", "LLM_PROVIDER": "gemini"},
        ):
            assert _select_llm_provider() == "gemini"

    def test_override_is_case_insensitive(self):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g", "LLM_PROVIDER": "GEMINI"},
        ):
            assert _select_llm_provider() == "gemini"

    def test_invalid_override_falls_back_to_auto_detection(self, caplog):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "", "LLM_PROVIDER": "bogus"},
        ):
            with caplog.at_level(logging.WARNING, logger="app.services.orchestrator"):
                assert _select_llm_provider() == "anthropic"
            assert "Unrecognized LLM_PROVIDER" in caplog.text

    def test_none_when_no_keys_and_no_override(self):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "LLM_PROVIDER": ""},
        ):
            assert _select_llm_provider() is None


class TestLlmProviderOverrideEndToEnd:
    def test_override_naming_unready_provider_never_falls_back_to_other_provider(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_API_KEY": "a",
                    "GEMINI_API_KEY": "",
                    "LLM_PROVIDER": "gemini",
                },
            ),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            document_text = "# Device\n\nSome QSG content."
            result = extract_qsg_fields(document_text, "d.md")

            assert result == _heuristic_extract_qsg(document_text, "d.md")
            mock_anthropic.Anthropic.assert_not_called()


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


class TestHeuristicContext:
    def test_default_ip_skips_subnet_mask(self):
        text = "# Dev\n\nSubnet mask 255.255.255.0, gateway 192.168.1.1\n"
        assert _heuristic_extract_qsg(text, "d.md").default_ip == "192.168.1.1"

    def test_default_ip_prefers_device_address_line(self):
        text = "# Dev\n\nUplink peer 10.0.0.9\nDefault IP: 192.168.0.1\n"
        assert _heuristic_extract_qsg(text, "d.md").default_ip == "192.168.0.1"

    def test_dhcp_server_is_not_a_provisioning_method(self):
        text = "# Dev\n\nBuilt-in DHCP server for LAN clients. Setup is console only.\n"
        methods = _heuristic_extract_qsg(text, "d.md").provisioning_methods
        assert [m.value for m in methods] == ["Console"]


class TestTiedTalkTrack:
    def test_tie_does_not_claim_a_winner(self):
        text = "# Same\n\nProvision over the web GUI.\n"
        a = extract_device_spec(text, "a.md")
        b = extract_device_spec(text.replace("Same", "Other"), "b.md")
        track = synthesize_battlecard(a, b).sales_talk_track
        assert "tied" in track
        assert "meaningfully less" not in track


class TestLlmHardening:
    def _run_anthropic(self, response, text="doc"):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            client = MagicMock()
            client.messages.create.return_value = response
            mock_anthropic.Anthropic.return_value = client
            result = orchestrator._extract_with_source(text, "d.md")
            return result, client, mock_anthropic

    def test_anthropic_forces_tool_use_and_sets_timeout(self):
        (result, source), client, mock_anthropic = self._run_anthropic(
            _fake_anthropic_response(_FAKE_EXTRACTION)
        )
        assert source == "llm"
        _, kwargs = client.messages.create.call_args
        assert kwargs["tool_choice"]["type"] == "tool"
        assert kwargs["tools"][0]["name"] == kwargs["tool_choice"]["name"]
        assert mock_anthropic.Anthropic.call_args.kwargs["timeout"] > 0

    def test_truncated_output_falls_back_to_heuristic(self):
        response = _fake_anthropic_response(_FAKE_EXTRACTION)
        response.stop_reason = "max_tokens"
        (_, source), _, _ = self._run_anthropic(response)
        assert source == "heuristic"

    def test_missing_tool_block_falls_back_to_heuristic(self):
        text_block = MagicMock()
        text_block.type = "text"
        response = MagicMock(content=[text_block], stop_reason="end_turn")
        (_, source), _, _ = self._run_anthropic(response)
        assert source == "heuristic"

    def test_oversized_input_is_truncated(self):
        big = "x" * (orchestrator.MAX_LLM_INPUT_CHARS + 5000)
        _, client, _ = self._run_anthropic(
            _fake_anthropic_response(_FAKE_EXTRACTION), text=big
        )
        content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert len(content) < orchestrator.MAX_LLM_INPUT_CHARS + 200

    def test_fenced_json_is_parsed(self):
        payload = orchestrator._parse_json_payload('```json\n{"a": 1}\n```')
        assert payload == {"a": 1}

    def test_heuristic_source_and_matrix_provenance(self):
        a = extract_device_spec(_DEVICE_A_TEXT, "a.md")
        b = extract_device_spec(_DEVICE_B_TEXT, "b.md")
        assert a.extraction_source == "heuristic"
        card = synthesize_battlecard(a, b)
        assert card.extraction_source_device_a == "heuristic"
        assert card.extraction_source_device_b == "heuristic"

    def test_extract_device_specs_keeps_order(self):
        a, b = orchestrator.extract_device_specs(
            _DEVICE_A_TEXT, "a.md", _DEVICE_B_TEXT, "b.md"
        )
        assert a == extract_device_spec(_DEVICE_A_TEXT, "a.md")
        assert b == extract_device_spec(_DEVICE_B_TEXT, "b.md")


class TestCarriedThroughFields:
    def _spec(self, **overrides):
        payload = {
            **_FAKE_EXTRACTION,
            "provisioning_methods": ["QR/Mobile App", "Web GUI"],
            "led_indicators": [
                {"led_name": "Power", "raw_description": "solid green when on"}
            ],
            "cabling_ports": [
                {"port_name": "WAN", "port_type": "RJ45", "notes": "PoE in"}
            ],
            **overrides,
        }
        return orchestrator._normalize_to_device_spec(
            orchestrator.QSGExtraction.model_validate(payload)
        )

    def test_led_name_is_kept(self):
        assert self._spec().led_states[0].name == "Power"

    def test_full_provisioning_list_is_kept(self):
        spec = self._spec()
        assert spec.provisioning_methods == ["QR/Mobile App", "Web GUI"]

    def test_qr_only_still_shows_qr_in_matrix(self):
        a = self._spec(provisioning_methods=["QR/Mobile App"])
        b = self._spec()
        cm = synthesize_battlecard(a, b).comparison_matrix
        assert cm["provisioning_methods"]["device_a"] == ["QR/Mobile App"]

    def test_port_notes_are_kept(self):
        assert self._spec().ports == ["WAN (RJ45) - PoE in"]


class TestLlmFailureModes:
    def _run_gemini(self, text):
        with (
            patch.dict(
                "os.environ",
                {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "k", "LLM_PROVIDER": ""},
            ),
            patch("app.services.orchestrator.genai") as mock_genai,
        ):
            client = MagicMock()
            client.models.generate_content.return_value = MagicMock(text=text)
            mock_genai.Client.return_value = client
            result = orchestrator._extract_with_source("doc", "d.md")
            return result, client

    def test_gemini_receives_extractor_system_prompt(self):
        _, client = self._run_gemini(json.dumps(_FAKE_EXTRACTION))
        config = client.models.generate_content.call_args.kwargs["config"]
        assert config.system_instruction == orchestrator._load_agent_system_prompt(
            "qsg_extractor.md"
        )

    def test_gemini_fenced_json_is_parsed(self):
        (_, source), _ = self._run_gemini(
            "```json\n" + json.dumps(_FAKE_EXTRACTION) + "\n```"
        )
        assert source == "llm"

    def test_gemini_schema_invalid_json_falls_back(self):
        (_, source), _ = self._run_gemini('{"device_name": 5, "led_indicators": "x"}')
        assert source == "heuristic"

    def test_gemini_non_json_falls_back(self):
        (_, source), _ = self._run_gemini("Sorry, I can't help with that.")
        assert source == "heuristic"

    def test_anthropic_receives_extractor_system_prompt(self):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k", "LLM_PROVIDER": ""}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            client = MagicMock()
            client.messages.create.return_value = _fake_anthropic_response(
                _FAKE_EXTRACTION
            )
            mock_anthropic.Anthropic.return_value = client
            orchestrator._extract_with_source("doc", "d.md")
        assert client.messages.create.call_args.kwargs[
            "system"
        ] == orchestrator._load_agent_system_prompt("qsg_extractor.md")

    def test_anthropic_schema_invalid_tool_input_falls_back(self):
        bad = _fake_anthropic_response({"device_name": 5, "led_indicators": "x"})
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k", "LLM_PROVIDER": ""}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value.messages.create.return_value = bad
            _, source = orchestrator._extract_with_source("# Doc\n", "d.md")
        assert source == "heuristic"


class TestFrictionScoreRules:
    def _spec(self, method, tools=0):
        return orchestrator.DeviceSpec(
            model_name="X",
            provisioning_method=method,
            required_tools=[f"tool{i}" for i in range(tools)],
        )

    def test_remote_provisioning_with_no_tools_is_lowest(self):
        for method in (
            orchestrator.ProvisioningMethod.TR069,
            orchestrator.ProvisioningMethod.DHCP,
        ):
            assert orchestrator._compute_friction_score(self._spec(method)) == 1

    def test_web_gui_adds_manual_penalty(self):
        spec = self._spec(orchestrator.ProvisioningMethod.WEB_GUI)
        assert orchestrator._compute_friction_score(spec) == 4

    def test_console_adds_extra_penalty(self):
        spec = self._spec(orchestrator.ProvisioningMethod.CONSOLE)
        assert orchestrator._compute_friction_score(spec) == 6

    def test_tool_count_is_capped_at_four(self):
        many = self._spec(orchestrator.ProvisioningMethod.TR069, tools=9)
        four = self._spec(orchestrator.ProvisioningMethod.TR069, tools=4)
        assert orchestrator._compute_friction_score(many) == 5
        assert orchestrator._compute_friction_score(four) == 5

    def test_score_is_clamped_to_ten(self):
        spec = self._spec(orchestrator.ProvisioningMethod.CONSOLE, tools=9)
        assert orchestrator._compute_friction_score(spec) == 10


class TestProvisioningMapping:
    def test_qr_mobile_app_maps_to_web_gui(self):
        raw = orchestrator.RawProvisioningMethod
        assert (
            orchestrator._select_primary_provisioning_method([raw.QR_MOBILE_APP])
            == orchestrator.ProvisioningMethod.WEB_GUI
        )

    def test_other_maps_to_console(self):
        raw = orchestrator.RawProvisioningMethod
        assert (
            orchestrator._select_primary_provisioning_method([raw.OTHER])
            == orchestrator.ProvisioningMethod.CONSOLE
        )

    def test_first_listed_method_is_primary(self):
        raw = orchestrator.RawProvisioningMethod
        assert (
            orchestrator._select_primary_provisioning_method([raw.WEB_GUI, raw.TR069])
            == orchestrator.ProvisioningMethod.WEB_GUI
        )
