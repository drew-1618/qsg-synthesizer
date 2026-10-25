"""Tests for a request's own AI provider and key (the dashboard's settings).

The keys travel in the X-LLM-Provider / X-LLM-API-Key headers of
POST /api/v1/compare and must only ever reach the provider's client: never
the logs, the response or the saved history record.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import history
from tests.test_orchestrator import (
    _FAKE_EXTRACTION,
    _fake_anthropic_response,
    _fake_gemini_response,
)

SAMPLE_DOCS_DIR = Path(__file__).parent.parent / "sample_docs"
USER_KEY = "sk-user-secret-123"
NO_SERVER_KEYS = {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "LLM_PROVIDER": ""}

client = TestClient(app)


def _compare(headers: dict):
    return client.post(
        "/api/v1/compare",
        data={
            "device_a_text": (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text("utf-8"),
            "device_b_text": (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text("utf-8"),
        },
        headers=headers,
    )


def _anthropic_mock(mock_anthropic, side_effect=None) -> MagicMock:
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client.messages.create.side_effect = side_effect
    else:
        mock_client.messages.create.return_value = _fake_anthropic_response(
            _FAKE_EXTRACTION
        )
    mock_anthropic.Anthropic.return_value = mock_client
    return mock_client


class _AuthError(Exception):
    status_code = 401


def test_user_key_reaches_the_provider_client():
    with (
        patch.dict("os.environ", NO_SERVER_KEYS),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        _anthropic_mock(mock_anthropic)
        response = _compare({"X-LLM-Provider": "anthropic", "X-LLM-API-Key": USER_KEY})

    assert response.status_code == 200
    assert response.json()["extraction_source_device_a"] == "llm"
    for call in mock_anthropic.Anthropic.call_args_list:
        assert call.kwargs["api_key"] == USER_KEY


def test_user_key_is_not_saved_in_history():
    with (
        patch.dict("os.environ", NO_SERVER_KEYS),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        _anthropic_mock(mock_anthropic)
        _compare({"X-LLM-Provider": "anthropic", "X-LLM-API-Key": USER_KEY})

    for path in history.HISTORY_DIR.glob("*.json"):
        assert USER_KEY not in path.read_text("utf-8")


def test_heuristic_choice_skips_the_llm_even_with_server_keys():
    with (
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "server", "LLM_PROVIDER": ""}),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        response = _compare({"X-LLM-Provider": "heuristic"})

    assert response.json()["extraction_source_device_a"] == "heuristic"
    mock_anthropic.Anthropic.assert_not_called()


def test_provider_without_a_key_uses_the_server_key():
    env = {"ANTHROPIC_API_KEY": "server-a", "GEMINI_API_KEY": "server-g"}
    with (
        patch.dict("os.environ", {**env, "LLM_PROVIDER": ""}),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
        patch("app.services.orchestrator.genai") as mock_genai,
    ):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _fake_gemini_response(
            _FAKE_EXTRACTION
        )
        mock_genai.Client.return_value = mock_client
        response = _compare({"X-LLM-Provider": "gemini"})

    assert response.json()["extraction_source_device_a"] == "llm"
    mock_anthropic.Anthropic.assert_not_called()
    assert mock_genai.Client.call_args.kwargs["api_key"] == "server-g"


def test_provider_without_any_key_uses_the_heuristic_parser():
    with patch.dict("os.environ", NO_SERVER_KEYS):
        response = _compare({"X-LLM-Provider": "gemini"})
    assert response.json()["extraction_source_device_a"] == "heuristic"
    assert response.json()["warnings"] == []


def test_rejected_user_key_warns_without_leaking_the_key(caplog):
    with (
        patch.dict("os.environ", NO_SERVER_KEYS),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
        caplog.at_level(logging.DEBUG),
    ):
        _anthropic_mock(mock_anthropic, side_effect=_AuthError("invalid x-api-key"))
        response = _compare({"X-LLM-Provider": "anthropic", "X-LLM-API-Key": USER_KEY})

    data = response.json()
    assert data["extraction_source_device_a"] == "heuristic"
    assert len(data["warnings"]) == 2
    assert all(
        "Anthropic key in your settings was rejected" in w for w in data["warnings"]
    )
    assert USER_KEY not in caplog.text
    assert USER_KEY not in response.text


def test_other_failures_with_a_user_key_get_a_retry_hint():
    with (
        patch.dict("os.environ", NO_SERVER_KEYS),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        _anthropic_mock(mock_anthropic, side_effect=RuntimeError("timeout"))
        response = _compare({"X-LLM-Provider": "anthropic", "X-LLM-API-Key": USER_KEY})

    assert "request using your key failed" in response.json()["warnings"][0]
    assert "Try again" in response.json()["warnings"][0]


def test_server_key_failures_warn_without_pointing_at_settings():
    with (
        patch.dict("os.environ", {**NO_SERVER_KEYS, "ANTHROPIC_API_KEY": "server"}),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        _anthropic_mock(mock_anthropic, side_effect=_AuthError("bad"))
        response = _compare({})

    warnings = response.json()["warnings"]
    assert len(warnings) == 2
    assert all("read with the built-in parser" in w for w in warnings)
    assert not any("Settings" in w for w in warnings)


class _RateLimitError(Exception):
    status_code = 429


@pytest.mark.parametrize("user_key", [None, USER_KEY])
def test_rate_limits_say_to_wait(user_key):
    env = {**NO_SERVER_KEYS, "ANTHROPIC_API_KEY": "" if user_key else "server"}
    headers = {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": user_key or ""}
    with (
        patch.dict("os.environ", env),
        patch("app.services.orchestrator.anthropic") as mock_anthropic,
    ):
        _anthropic_mock(mock_anthropic, side_effect=_RateLimitError("slow down"))
        response = _compare(headers)

    assert "rate limit" in response.json()["warnings"][0]
    assert "Wait a minute" in response.json()["warnings"][0]


class TestHeaderValidation:
    def test_unknown_provider_is_rejected(self):
        response = _compare({"X-LLM-Provider": "openai"})
        assert response.status_code == 422
        assert "Unknown AI provider" in response.json()["detail"]

    def test_key_without_provider_is_rejected(self):
        response = _compare({"X-LLM-API-Key": USER_KEY})
        assert response.status_code == 422
        assert USER_KEY not in response.text

    def test_malformed_key_is_rejected_without_echoing_it(self):
        bad_key = "sk-" + "x" * 400
        response = _compare({"X-LLM-Provider": "anthropic", "X-LLM-API-Key": bad_key})
        assert response.status_code == 422
        assert bad_key not in response.text
        assert history.count_comparisons() == 0

    def test_key_with_spaces_inside_is_rejected(self):
        response = _compare({"X-LLM-Provider": "gemini", "X-LLM-API-Key": "two words"})
        assert response.status_code == 422


class TestSettingsEndpoint:
    def test_reports_the_server_provider(self):
        with (
            patch.dict("os.environ", {**NO_SERVER_KEYS, "GEMINI_API_KEY": "g"}),
            patch("app.services.orchestrator.genai", MagicMock()),
        ):
            assert client.get("/api/v1/settings").json() == {
                "server_provider": "gemini"
            }

    def test_reports_none_without_server_keys(self):
        with patch.dict("os.environ", NO_SERVER_KEYS):
            assert client.get("/api/v1/settings").json() == {"server_provider": None}

    def test_requires_auth_once_configured(self, monkeypatch):
        monkeypatch.setenv("APP_USERNAME", "admin")
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        assert client.get("/api/v1/settings").status_code == 401


class TestRerun:
    def test_rerun_reads_saved_documents_with_the_requested_ai(self):
        with patch.dict("os.environ", NO_SERVER_KEYS):
            first = _compare({}).json()
        assert first["extraction_source_device_a"] == "heuristic"

        with (
            patch.dict("os.environ", NO_SERVER_KEYS),
            patch("app.services.orchestrator.anthropic") as mock_anthropic,
        ):
            _anthropic_mock(mock_anthropic)
            response = client.post(
                f"/api/v1/history/{first['history_id']}/rerun",
                headers={"X-LLM-Provider": "anthropic", "X-LLM-API-Key": USER_KEY},
            )

        data = response.json()
        assert response.status_code == 200
        assert data["extraction_source_device_a"] == "llm"
        assert data["history_id"] != first["history_id"]
        assert data["perspective"] == first["perspective"] == "sales"
        stack = client.get("/api/v1/history/stacks").json()
        assert stack["record_total"] == 2

    def test_rerun_keeps_the_saved_mode(self):
        with patch.dict("os.environ", NO_SERVER_KEYS):
            first = client.post(
                "/api/v1/compare",
                data={
                    "device_a_text": (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
                        "utf-8"
                    ),
                    "device_b_text": (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
                        "utf-8"
                    ),
                    "mode": "neutral",
                },
            ).json()
            rerun = client.post(f"/api/v1/history/{first['history_id']}/rerun").json()
        assert rerun["perspective"] == "neutral"

    def test_record_without_saved_documents_returns_409(self):
        record = history.save_comparison(
            history.get_comparison(_compare({}).json()["history_id"]).battlecard
        )
        response = client.post(f"/api/v1/history/{record.id}/rerun")
        assert response.status_code == 409
        assert "Upload the documents again" in response.json()["detail"]

    def test_unknown_record_returns_404(self):
        assert client.post(f"/api/v1/history/{'c' * 32}/rerun").status_code == 404

    def test_bad_ai_header_is_rejected(self):
        first = _compare({}).json()
        response = client.post(
            f"/api/v1/history/{first['history_id']}/rerun",
            headers={"X-LLM-Provider": "openai"},
        )
        assert response.status_code == 422


class TestKeepDocuments:
    def _compare_without_keeping(self):
        return client.post(
            "/api/v1/compare",
            data={
                "device_a_text": (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
                    "utf-8"
                ),
                "device_b_text": (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
                    "utf-8"
                ),
                "keep_documents": "false",
            },
        )

    def _source_files(self):
        return list((history.HISTORY_DIR / "sources").glob("*.txt"))

    def test_documents_are_kept_by_default(self):
        record = history.get_comparison(_compare({}).json()["history_id"])
        assert len(record.sources) == 2
        assert len(self._source_files()) == 2

    def test_turning_it_off_saves_only_the_battlecard(self):
        data = self._compare_without_keeping().json()
        record = history.get_comparison(data["history_id"])
        assert record.sources == []
        assert record.input_fingerprint is not None
        assert self._source_files() == []
        response = client.post(f"/api/v1/history/{data['history_id']}/rerun")
        assert response.status_code == 409
        assert "weren't kept" in response.json()["detail"]

    def test_remove_saved_documents_keeps_the_battlecards(self):
        kept = _compare({}).json()["history_id"]
        self._compare_without_keeping()
        response = client.delete("/api/v1/history/documents")
        assert response.json() == {"forgotten": 1}
        assert self._source_files() == []
        assert history.count_comparisons() == 2
        assert history.get_comparison(kept).sources == []
        assert client.post(f"/api/v1/history/{kept}/rerun").status_code == 409

    def test_remove_saved_documents_requires_auth(self, monkeypatch):
        monkeypatch.setenv("APP_USERNAME", "admin")
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        assert client.delete("/api/v1/history/documents").status_code == 401
