"""Scanned PDFs: pages that are images, with no text layer.

The built-in parser can't read them, so with no AI provider the upload is
refused with a message saying what to do; with one, the provider transcribes
the page images to Markdown, which then goes through the normal pipeline.
The provider SDKs are mocked as in tests/test_orchestrator.py.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pymupdf
import pytest
from fastapi.testclient import TestClient

from qsg_synthesizer.main import app
from qsg_synthesizer.services import history, orchestrator
from qsg_synthesizer.tools import pdf_parser
from qsg_synthesizer.tools.pdf_parser import (
    ScannedPdfError,
    extract_text_from_pdf,
    render_pdf_pages,
)
from qsg_synthesizer.tools.text_cleaning import clean_text
from tests.test_orchestrator import _FAKE_EXTRACTION, _fake_anthropic_response

SAMPLE_DOCS_DIR = Path(__file__).parent.parent / "sample_docs"
NO_SERVER_KEYS = {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "LLM_PROVIDER": ""}
TRANSCRIPT = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text("utf-8")

client = TestClient(app)


def _scanned(name: str = "device_a_qsg.pdf", copies: int = 1) -> bytes:
    """`name`'s pages as images only, as a scanner produces them."""
    source = pymupdf.open(SAMPLE_DOCS_DIR / name)
    scan = pymupdf.open()
    for _ in range(copies):
        for page in source:
            image = page.get_pixmap(dpi=72)
            new = scan.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=image)
    return scan.tobytes()


def _upload(pdf: bytes, headers: dict | None = None):
    text_b = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text("utf-8")
    return client.post(
        "/api/v1/compare",
        data={"device_b_text": text_b},
        files={"device_a_file": ("scan.pdf", pdf, "application/pdf")},
        headers=headers or {},
    )


def _anthropic_reader(mock_anthropic) -> MagicMock:
    """A fake client that transcribes (a plain message with images) and
    extracts (a forced tool call)."""

    def create(**kwargs):
        if "tools" in kwargs:
            return _fake_anthropic_response(_FAKE_EXTRACTION)
        text = MagicMock(type="text", text=TRANSCRIPT)
        return MagicMock(content=[text], stop_reason="end_turn")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = create
    mock_anthropic.Anthropic.return_value = mock_client
    return mock_client


class TestDetection:
    def test_a_scan_is_detected(self):
        with pytest.raises(ScannedPdfError) as caught:
            extract_text_from_pdf(_scanned())
        assert caught.value.page_count == 2

    def test_a_text_pdf_is_not_a_scan(self):
        pdf = (SAMPLE_DOCS_DIR / "device_a_qsg.pdf").read_bytes()
        assert "NovaFiber GX400" in extract_text_from_pdf(pdf)

    def test_pages_render_as_jpeg_up_to_the_cap(self):
        images = render_pdf_pages(_scanned(copies=3), max_pages=4)
        assert len(images) == 4
        assert all(image[:3] == b"\xff\xd8\xff" for image in images)


class TestUpload:
    def test_without_an_ai_provider_the_scan_is_refused_with_what_to_do(self):
        with patch.dict("os.environ", NO_SERVER_KEYS):
            response = _upload(_scanned())
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail.startswith("'scan.pdf' is a scanned PDF")
        assert "Choose an AI provider in Settings" in detail

    def test_the_provider_transcribes_the_pages_then_extracts_them(self):
        with (
            patch.dict("os.environ", NO_SERVER_KEYS),
            patch("qsg_synthesizer.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_client = _anthropic_reader(mock_anthropic)
            response = _upload(
                _scanned(),
                {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": "sk-user"},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["comparison_matrix"]["model_name"]["device_a"] == "NovaFiber GX400"
        assert data["warnings"][0] == (
            "NovaFiber GX400: This is a scanned PDF; Anthropic "
            f"({orchestrator.default_model('anthropic')}) read its pages as images."
        )
        transcribe_call = next(
            call
            for call in mock_client.messages.create.call_args_list
            if "tools" not in call.kwargs
        )
        content = transcribe_call.kwargs["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "image", "text"]
        # The transcript is what's kept, so Rerun reads it as text.
        record = history.get_comparison(data["history_id"])
        assert history.load_sources(record)[0][1] == clean_text(TRANSCRIPT)

    def test_a_failed_transcription_is_a_502_with_the_reason(self):
        class RateLimited(Exception):
            status_code = 429

        with (
            patch.dict("os.environ", NO_SERVER_KEYS),
            patch("qsg_synthesizer.services.orchestrator.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value.messages.create.side_effect = (
                RateLimited("slow down")
            )
            response = _upload(
                _scanned(),
                {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": "sk-user"},
            )
        assert response.status_code == 502
        assert "the key in your settings hit its rate limit" in (
            response.json()["detail"]
        )

    def test_pages_past_the_cap_are_skipped_with_a_note(self, monkeypatch):
        monkeypatch.setattr("qsg_synthesizer.main.MAX_SCANNED_PAGES", 2)
        with (
            patch.dict("os.environ", NO_SERVER_KEYS),
            patch("qsg_synthesizer.services.orchestrator.anthropic") as mock_anthropic,
        ):
            _anthropic_reader(mock_anthropic)
            response = _upload(
                _scanned(copies=2),
                {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": "sk-user"},
            )
        assert response.status_code == 200
        assert any(
            "Only the first 2 of its 4 scanned pages were read" in warning
            for warning in response.json()["warnings"]
        )

    def test_pages_are_sent_a_few_per_call(self, monkeypatch):
        monkeypatch.setattr(orchestrator, "_TRANSCRIBE_PAGES_PER_CALL", 1)
        calls = []

        def fake(images, source, first_page, **options):
            calls.append((len(images), first_page))
            return f"page {first_page}"

        monkeypatch.setitem(orchestrator._PROVIDER_TRANSCRIBERS, "gemini", fake)
        monkeypatch.setattr(orchestrator, "_resolve_llm", lambda llm: ("gemini", "k"))
        text, read_with = orchestrator.transcribe_pages([b"1", b"2", b"3"], "s.pdf")
        assert sorted(calls) == [(1, 1), (1, 2), (1, 3)]
        assert text == "page 1\n\npage 2\n\npage 3"
        assert read_with.startswith("Gemini (")


def test_scan_threshold_ignores_a_stray_page_number(monkeypatch):
    # A scan with a few characters of real text (a stamped page number) is
    # still a scan.
    scan = pymupdf.open(stream=_scanned(), filetype="pdf")
    scan[0].insert_text((20, 20), "1")
    with pytest.raises(ScannedPdfError):
        extract_text_from_pdf(scan.tobytes())
    assert pdf_parser._SCANNED_MAX_TEXT_CHARS > 1
