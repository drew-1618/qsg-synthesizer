import json
from unittest.mock import MagicMock, patch

from app.services.orchestrator import _heuristic_extract_qsg, extract_qsg_fields

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

    def test_falls_back_to_heuristic_on_api_error(self):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = RuntimeError("api unreachable")
            mock_anthropic.Anthropic.return_value = mock_client

            document_text = "# LegacyLink RG-210\n\nSome QSG content."
            result = extract_qsg_fields(document_text, "device_b_qsg.md")

            assert result == _heuristic_extract_qsg(document_text, "device_b_qsg.md")

    def test_falls_back_to_heuristic_without_api_key(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("ANTHROPIC_API_KEY", None)
            document_text = "# LegacyLink RG-210\n\nSome QSG content."
            result = extract_qsg_fields(document_text, "device_b_qsg.md")
            assert result == _heuristic_extract_qsg(document_text, "device_b_qsg.md")
