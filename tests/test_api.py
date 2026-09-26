import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import MAX_TEXT_CHARS, MAX_UPLOAD_BYTES, app, compare_rate_limiter
from app.services import history

SAMPLE_DOCS_DIR = Path(__file__).parent.parent / "sample_docs"

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_serves_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()


def test_get_samples_returns_base64_encoded_pdfs():
    response = client.get("/api/v1/samples")
    assert response.status_code == 200
    data = response.json()
    assert data["device_a"]["filename"] == "device_a_qsg.pdf"
    assert data["device_b"]["filename"] == "device_b_qsg.pdf"
    assert len(data["device_a"]["content_base64"]) > 0
    assert len(data["device_b"]["content_base64"]) > 0


class TestCompareText:
    def _post(self):
        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
            encoding="utf-8"
        )
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
            encoding="utf-8"
        )
        return client.post(
            "/api/v1/compare-text",
            json={
                "device_a_text": device_a_text,
                "device_a_name": "device_a_qsg.md",
                "device_b_text": device_b_text,
                "device_b_name": "device_b_qsg.md",
            },
        )

    def test_returns_200_with_valid_battlecard_shape(self):
        response = self._post()
        assert response.status_code == 200
        data = response.json()
        for key in (
            "comparison_matrix",
            "installation_friction_score_device_a",
            "installation_friction_score_device_b",
            "key_risks_device_a",
            "key_risks_device_b",
            "sales_talk_track",
        ):
            assert key in data

    def test_friction_scores_are_in_range_and_ordered(self):
        data = self._post().json()
        assert 1 <= data["installation_friction_score_device_a"] <= 10
        assert 1 <= data["installation_friction_score_device_b"] <= 10
        # Device A (auto-provisioning, no tools) should be less friction
        # than Device B (console/DIP-switch, three required tools).
        assert (
            data["installation_friction_score_device_a"]
            < data["installation_friction_score_device_b"]
        )

    def test_comparison_matrix_reflects_provisioning_methods(self):
        data = self._post().json()
        provisioning = data["comparison_matrix"]["provisioning_method"]
        assert provisioning["device_a"] == "TR-069"
        assert provisioning["device_b"] == "Console"

    def test_key_risks_and_sales_talk_track_are_populated(self):
        data = self._post().json()
        assert len(data["key_risks_device_b"]) > 0
        assert isinstance(data["sales_talk_track"], str)
        assert len(data["sales_talk_track"]) > 0

    def test_rejects_missing_field(self):
        response = client.post(
            "/api/v1/compare-text",
            json={"device_a_text": "some text"},
        )
        assert response.status_code == 422

    def test_rejects_oversized_text_field(self):
        oversized = "x" * (MAX_TEXT_CHARS + 1)
        response = client.post(
            "/api/v1/compare-text",
            json={"device_a_text": oversized, "device_b_text": "some text"},
        )
        assert response.status_code == 422


class TestComparePdfs:
    def _post(self):
        with (
            open(SAMPLE_DOCS_DIR / "device_a_qsg.pdf", "rb") as a_file,
            open(SAMPLE_DOCS_DIR / "device_b_qsg.pdf", "rb") as b_file,
        ):
            return client.post(
                "/api/v1/compare-pdfs",
                files={
                    "device_a_pdf": ("device_a_qsg.pdf", a_file, "application/pdf"),
                    "device_b_pdf": ("device_b_qsg.pdf", b_file, "application/pdf"),
                },
            )

    def test_returns_200_with_valid_battlecard_shape(self):
        response = self._post()
        assert response.status_code == 200
        data = response.json()
        for key in (
            "comparison_matrix",
            "installation_friction_score_device_a",
            "installation_friction_score_device_b",
            "key_risks_device_a",
            "key_risks_device_b",
            "sales_talk_track",
        ):
            assert key in data

    def test_matches_compare_text_result_for_same_content(self):
        pdf_data = self._post().json()

        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
            encoding="utf-8"
        )
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
            encoding="utf-8"
        )
        text_data = client.post(
            "/api/v1/compare-text",
            json={"device_a_text": device_a_text, "device_b_text": device_b_text},
        ).json()

        assert (
            pdf_data["installation_friction_score_device_a"]
            == text_data["installation_friction_score_device_a"]
        )
        assert (
            pdf_data["installation_friction_score_device_b"]
            == text_data["installation_friction_score_device_b"]
        )

    def test_rejects_non_pdf_upload(self):
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("device_a.txt", b"not a pdf", "text/plain"),
                "device_b_pdf": (
                    "device_b_qsg.pdf",
                    open(SAMPLE_DOCS_DIR / "device_b_qsg.pdf", "rb"),
                    "application/pdf",
                ),
            },
        )
        assert response.status_code == 400

    def test_rejects_oversized_pdf_upload(self):
        oversized = b"%PDF-1.4\n" + b"0" * (MAX_UPLOAD_BYTES + 1)
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("device_a_qsg.pdf", oversized, "application/pdf"),
                "device_b_pdf": (
                    "device_b_qsg.pdf",
                    open(SAMPLE_DOCS_DIR / "device_b_qsg.pdf", "rb"),
                    "application/pdf",
                ),
            },
        )
        assert response.status_code == 413


class TestHistory:
    def _post_comparison(self):
        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
            encoding="utf-8"
        )
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
            encoding="utf-8"
        )
        return client.post(
            "/api/v1/compare-text",
            json={"device_a_text": device_a_text, "device_b_text": device_b_text},
        )

    def test_empty_history_returns_empty_page(self):
        response = client.get("/api/v1/history")
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    def test_comparison_is_recorded_and_listed(self):
        battlecard = self._post_comparison().json()

        response = client.get("/api/v1/history")
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 1
        items = page["items"]
        assert len(items) == 1
        assert items[0]["installation_friction_score_device_a"] == (
            battlecard["installation_friction_score_device_a"]
        )

    def test_pagination_limit_and_offset(self):
        for _ in range(3):
            self._post_comparison()

        page1 = client.get("/api/v1/history?limit=2&offset=0").json()
        assert len(page1["items"]) == 2
        assert page1["total"] == 3

        page2 = client.get("/api/v1/history?limit=2&offset=2").json()
        assert len(page2["items"]) == 1
        assert page2["total"] == 3

        ids_seen = {item["id"] for item in page1["items"] + page2["items"]}
        assert len(ids_seen) == 3

    def test_search_filters_by_device_name_case_insensitively(self):
        self._post_comparison()
        device_a_name = client.get("/api/v1/history").json()["items"][0][
            "device_a_name"
        ]

        match = client.get(f"/api/v1/history?q={device_a_name[:4].upper()}")
        assert match.json()["total"] == 1

        no_match = client.get("/api/v1/history?q=zzz-nonexistent-device")
        assert no_match.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    def test_history_item_roundtrips_full_battlecard(self):
        battlecard = self._post_comparison().json()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        response = client.get(f"/api/v1/history/{history_id}")
        assert response.status_code == 200
        assert response.json()["battlecard"] == battlecard

    def test_unknown_history_id_returns_404(self):
        response = client.get("/api/v1/history/" + "0" * 32)
        assert response.status_code == 404

    def test_malformed_history_id_returns_404(self):
        response = client.get("/api/v1/history/../../etc/passwd")
        assert response.status_code == 404

    def test_export_markdown_contains_key_sections(self):
        self._post_comparison()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        response = client.get(f"/api/v1/history/{history_id}/export.md")
        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        body = response.text
        assert body.startswith("# Battlecard:")
        assert "Competitive Sales Talk Track" in body
        assert "Side-by-Side Comparison" in body

    def test_export_pdf_returns_pdf_bytes(self):
        self._post_comparison()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        response = client.get(f"/api/v1/history/{history_id}/export.pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        assert response.content[:4] == b"%PDF"

    def test_export_unknown_history_id_returns_404(self):
        response = client.get("/api/v1/history/" + "0" * 32 + "/export.md")
        assert response.status_code == 404

    def test_delete_removes_item_from_list(self):
        self._post_comparison()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        response = client.delete(f"/api/v1/history/{history_id}")
        assert response.status_code == 204
        assert client.get("/api/v1/history").json()["items"] == []

    def test_delete_unknown_id_returns_404(self):
        response = client.delete("/api/v1/history/" + "0" * 32)
        assert response.status_code == 404

    def test_clear_all_removes_every_item(self):
        self._post_comparison()
        self._post_comparison()
        assert client.get("/api/v1/history").json()["total"] == 2

        response = client.delete("/api/v1/history")
        assert response.status_code == 200
        assert response.json() == {"deleted": 2}
        assert client.get("/api/v1/history").json()["items"] == []


class TestRateLimiting:
    def _post_comparison(self):
        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
            encoding="utf-8"
        )
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
            encoding="utf-8"
        )
        return client.post(
            "/api/v1/compare-text",
            json={"device_a_text": device_a_text, "device_b_text": device_b_text},
        )

    def test_exceeding_the_limit_returns_429_with_retry_after(self, monkeypatch):
        monkeypatch.setattr(compare_rate_limiter, "max_requests", 2)

        assert self._post_comparison().status_code == 200
        assert self._post_comparison().status_code == 200

        response = self._post_comparison()
        assert response.status_code == 429
        assert "Retry-After" in response.headers


class TestUnhandledErrors:
    def test_unexpected_exception_returns_generic_500_and_is_logged(
        self, monkeypatch, caplog
    ):
        def _boom(*args, **kwargs):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(history, "save_comparison", _boom)
        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(
            encoding="utf-8"
        )
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(
            encoding="utf-8"
        )

        # Real servers hand this off to our registered exception handler, but
        # TestClient re-raises unhandled server exceptions by default (for
        # debuggability) unless told not to.
        lenient_client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="app.main"):
            response = lenient_client.post(
                "/api/v1/compare-text",
                json={"device_a_text": device_a_text, "device_b_text": device_b_text},
            )

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert "disk on fire" not in response.text
        assert "Unhandled exception" in caplog.text
