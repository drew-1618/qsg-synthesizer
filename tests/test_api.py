import logging
import re
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
    assert data["device_a"]["filename"] == "NovaFiber-GX400-QSG.pdf"
    assert data["device_b"]["filename"] == "LegacyLink-RG-210-QSG.pdf"
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


class TestCompareDocuments:
    """POST /api/v1/compare: each side is an uploaded file or pasted text."""

    def _sample(self, name):
        return (SAMPLE_DOCS_DIR / name).read_bytes()

    def test_two_markdown_files(self):
        response = client.post(
            "/api/v1/compare",
            files={
                "device_a_file": (
                    "a.md",
                    self._sample("device_a_qsg.md"),
                    "text/markdown",
                ),
                "device_b_file": (
                    "b.md",
                    self._sample("device_b_qsg.md"),
                    "text/markdown",
                ),
            },
        )
        assert response.status_code == 200
        assert "history_id" in response.json()

    def test_two_pasted_texts_with_names(self):
        response = client.post(
            "/api/v1/compare",
            data={
                "device_a_text": self._sample("device_a_qsg.md").decode(),
                "device_a_name": "Our guide",
                "device_b_text": self._sample("device_b_qsg.md").decode(),
            },
        )
        assert response.status_code == 200

    def test_file_and_pasted_text_can_be_mixed(self):
        response = client.post(
            "/api/v1/compare",
            data={"device_b_text": self._sample("device_b_qsg.md").decode()},
            files={
                "device_a_file": (
                    "a.pdf",
                    self._sample("device_a_qsg.pdf"),
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 200

    def test_docx_upload_is_accepted(self):
        import io

        import docx

        document = docx.Document()
        for line in self._sample("device_a_qsg.md").decode().splitlines():
            document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        response = client.post(
            "/api/v1/compare",
            data={"device_b_text": self._sample("device_b_qsg.md").decode()},
            files={
                "device_a_file": (
                    "a.docx",
                    buffer.getvalue(),
                    "application/octet-stream",
                )
            },
        )
        assert response.status_code == 200

    def test_file_and_text_for_the_same_side_is_422(self):
        response = client.post(
            "/api/v1/compare",
            data={
                "device_a_text": self._sample("device_a_qsg.md").decode(),
                "device_b_text": self._sample("device_b_qsg.md").decode(),
            },
            files={
                "device_a_file": (
                    "a.md",
                    self._sample("device_a_qsg.md"),
                    "text/markdown",
                )
            },
        )
        assert response.status_code == 422
        assert "Device A" in response.json()["detail"]

    def test_missing_side_is_422(self):
        response = client.post(
            "/api/v1/compare",
            data={"device_a_text": self._sample("device_a_qsg.md").decode()},
        )
        assert response.status_code == 422
        assert "Device B" in response.json()["detail"]

    def test_blank_pasted_text_counts_as_missing(self):
        response = client.post(
            "/api/v1/compare",
            data={
                "device_a_text": "   \n  ",
                "device_b_text": self._sample("device_b_qsg.md").decode(),
            },
        )
        assert response.status_code == 422

    def test_unsupported_extension_is_400_with_hint(self):
        response = client.post(
            "/api/v1/compare",
            data={"device_b_text": self._sample("device_b_qsg.md").decode()},
            files={
                "device_a_file": ("deck.pptx", b"x" * 100, "application/octet-stream")
            },
        )
        assert response.status_code == 400
        assert "not a supported file type" in response.json()["detail"]
        assert "Word (.docx)" in response.json()["detail"]

    def test_corrupt_docx_is_400_naming_the_file_kind(self):
        response = client.post(
            "/api/v1/compare",
            data={"device_b_text": self._sample("device_b_qsg.md").decode()},
            files={
                "device_a_file": ("a.docx", b"not a zip", "application/octet-stream")
            },
        )
        assert response.status_code == 400
        assert "could not be read as a Word document" in response.json()["detail"]

    def test_too_little_pasted_text_is_422_and_not_saved(self):
        response = client.post(
            "/api/v1/compare",
            data={
                "device_a_text": "too short",
                "device_b_text": self._sample("device_b_qsg.md").decode(),
            },
        )
        assert response.status_code == 422
        assert client.get("/api/v1/history").json()["items"] == []

    def test_compare_pdfs_still_rejects_other_formats(self):
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("a.md", b"# hi", "text/markdown"),
                "device_b_pdf": (
                    "b.pdf",
                    self._sample("device_b_qsg.pdf"),
                    "application/pdf",
                ),
            },
        )
        assert response.status_code == 400
        assert "is not a PDF file" in response.json()["detail"]


class TestComparisonMode:
    def _compare(self, a="device_a_qsg.md", b="device_b_qsg.md", **data):
        return client.post(
            "/api/v1/compare",
            data=data,
            files={
                "device_a_file": (
                    "a.md",
                    (SAMPLE_DOCS_DIR / a).read_bytes(),
                    "text/markdown",
                ),
                "device_b_file": (
                    "b.md",
                    (SAMPLE_DOCS_DIR / b).read_bytes(),
                    "text/markdown",
                ),
            },
        )

    def test_defaults_to_sales_perspective(self):
        response = self._compare()
        assert response.status_code == 200
        assert response.json()["perspective"] == "sales"

    def test_neutral_mode_is_recorded(self):
        assert self._compare(mode="neutral").json()["perspective"] == "neutral"

    def test_unknown_mode_is_422(self):
        assert self._compare(mode="aggressive").status_code == 422

    def test_sales_talk_track_follows_who_is_ours(self):
        # Side A is ours: NovaFiber installs easier, so lead with that...
        wins = self._compare().json()["sales_talk_track"]
        assert "whenever" in wins and "incumbent" in wins
        # ...and with the files swapped our device is the harder install.
        loses = self._compare(a="device_b_qsg.md", b="device_a_qsg.md").json()[
            "sales_talk_track"
        ]
        assert "don't compete on install speed" in loses

    def test_perspective_round_trips_through_history(self):
        history_id = self._compare().json()["history_id"]
        assert (
            client.get(f"/api/v1/history/{history_id}").json()["battlecard"][
                "perspective"
            ]
            == "sales"
        )
        item = client.get("/api/v1/history").json()["items"][0]
        assert item["perspective"] == "sales"

    def test_older_endpoints_stay_neutral(self):
        text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(encoding="utf-8")
        response = client.post(
            "/api/v1/compare-text",
            json={
                "device_a_text": text,
                "device_b_text": text.replace("NovaFiber", "Other"),
            },
        )
        assert response.status_code == 200
        assert response.json()["perspective"] is None

    def test_exports_label_the_roles_in_sales_mode(self):
        history_id = self._compare().json()["history_id"]
        markdown = client.get(f"/api/v1/history/{history_id}/export.md")
        assert "(ours)" in markdown.text and "(competitor)" in markdown.text
        disposition = markdown.headers["content-disposition"]
        assert "ours" not in disposition and "competitor" not in disposition


class TestDatasheets:
    def _compare(self, a, b, mode="sales"):
        return client.post(
            "/api/v1/compare",
            data={"mode": mode},
            files={
                "device_a_file": (
                    a,
                    (SAMPLE_DOCS_DIR / a).read_bytes(),
                    "text/markdown",
                ),
                "device_b_file": (
                    b,
                    (SAMPLE_DOCS_DIR / b).read_bytes(),
                    "text/markdown",
                ),
            },
        )

    def test_two_datasheets_compare_specs_without_friction(self):
        response = self._compare("device_c_datasheet.md", "device_d_datasheet.md")
        assert response.status_code == 200
        data = response.json()
        assert data["installation_friction_score_device_a"] is None
        assert data["installation_friction_score_device_b"] is None
        assert data["comparison_matrix"]["document_type"] == {
            "device_a": "datasheet",
            "device_b": "datasheet",
        }
        names = [row["name"] for row in data["comparison_matrix"]["extra_specs"]]
        assert "Power Consumption" in names and "Battery Backup" in names

    def test_history_lists_unscored_comparisons(self):
        self._compare("device_c_datasheet.md", "device_d_datasheet.md")
        item = client.get("/api/v1/history").json()["items"][0]
        assert item["installation_friction_score_device_a"] is None

    def test_exports_say_friction_was_not_compared_and_include_spec_rows(self):
        history_id = self._compare("device_a_qsg.md", "device_c_datasheet.md").json()[
            "history_id"
        ]
        markdown = client.get(f"/api/v1/history/{history_id}/export.md").text
        assert "Not compared: install effort is only scored" in markdown
        assert (
            "| Power Consumption | Not listed | 28 W typical, 36 W max (PoE++) |"
            in markdown
        )
        pdf = client.get(f"/api/v1/history/{history_id}/export.pdf")
        assert pdf.content[:4] == b"%PDF"


class TestPdfUploadErrors:
    def test_corrupt_pdf_returns_400(self):
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("a.pdf", b"not really a pdf", "application/pdf"),
                "device_b_pdf": ("b.pdf", b"not really a pdf", "application/pdf"),
            },
        )
        assert response.status_code == 400
        assert "could not be read as a PDF" in response.json()["detail"]

    def test_pdf_over_the_page_limit_says_why(self, monkeypatch):
        from app.tools import pdf_parser

        monkeypatch.setattr(pdf_parser, "MAX_PDF_PAGES", 1)
        pdf_bytes = (SAMPLE_DOCS_DIR / "device_a_qsg.pdf").read_bytes()
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("a.pdf", pdf_bytes, "application/pdf"),
                "device_b_pdf": ("b.pdf", pdf_bytes, "application/pdf"),
            },
        )
        assert response.status_code == 400
        assert "'a.pdf' has " in response.json()["detail"]
        assert "the limit is 1" in response.json()["detail"]

    def test_encrypted_pdf_returns_400(self):
        import pymupdf

        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Quick start guide")
        locked = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y"
        )
        doc.close()
        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("a.pdf", locked, "application/pdf"),
                "device_b_pdf": ("b.pdf", locked, "application/pdf"),
            },
        )
        assert response.status_code == 400
        assert client.get("/api/v1/history").json()["total"] == 0


class TestHistoryId:
    def test_compare_text_returns_id_of_saved_record(self):
        text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(encoding="utf-8")
        first = client.post(
            "/api/v1/compare-text",
            json={"device_a_text": text, "device_b_text": text},
        ).json()
        # A newer record must not change which record the first response points at.
        client.post(
            "/api/v1/compare-text",
            json={"device_a_text": text, "device_b_text": text},
        )
        record = client.get(f"/api/v1/history/{first['history_id']}").json()
        assert record["id"] == first["history_id"]
        assert client.get("/api/v1/history").json()["total"] == 2

    def test_compare_pdfs_returns_history_id(self):
        data = TestComparePdfs()._post().json()
        assert client.get(f"/api/v1/history/{data['history_id']}").status_code == 200


class TestHistoryStacks:
    def _post(self):
        return TestHistory()._post_comparison().json()

    def test_rerun_of_same_documents_stacks_with_one_redundant_copy(self):
        first = self._post()
        second = self._post()

        page = client.get("/api/v1/history/stacks").json()
        assert (page["total"], page["record_total"]) == (1, 2)
        stack = page["items"][0]
        assert stack["latest"]["id"] == second["history_id"]
        assert stack["redundant_ids"] == [first["history_id"]]

    def test_delete_copies_endpoint(self):
        self._post()
        second = self._post()

        response = client.delete(f"/api/v1/history/{second['history_id']}/copies")
        assert response.json() == {"deleted": 1}
        assert client.get("/api/v1/history").json()["total"] == 1

    def test_delete_copies_of_unknown_record_returns_404(self):
        assert client.delete(f"/api/v1/history/{'c' * 32}/copies").status_code == 404

    def test_stack_routes_require_auth_once_configured(self, monkeypatch):
        monkeypatch.setenv("APP_USERNAME", "admin")
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        assert client.get("/api/v1/history/stacks").status_code == 401
        assert client.delete(f"/api/v1/history/{'c' * 32}/copies").status_code == 401


class TestEmptyDocuments:
    def test_image_only_pdf_returns_422_and_is_not_saved(self):
        import pymupdf

        doc = pymupdf.open()
        doc.new_page()
        blank_pdf = doc.tobytes()
        doc.close()

        response = client.post(
            "/api/v1/compare-pdfs",
            files={
                "device_a_pdf": ("a.pdf", blank_pdf, "application/pdf"),
                "device_b_pdf": ("b.pdf", blank_pdf, "application/pdf"),
            },
        )
        assert response.status_code == 422
        assert "no extractable text" in response.json()["detail"]
        assert client.get("/api/v1/history").json()["total"] == 0

    def test_whitespace_only_text_returns_422_and_is_not_saved(self):
        response = client.post(
            "/api/v1/compare-text",
            json={"device_a_text": " \n\t ", "device_b_text": "x"},
        )
        assert response.status_code == 422
        assert client.get("/api/v1/history").json()["total"] == 0


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
        history_id = battlecard.pop("history_id")

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

    def test_export_default_filename_is_descriptive(self):
        self._post_comparison()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        for ext in ("md", "pdf"):
            response = client.get(f"/api/v1/history/{history_id}/export.{ext}")
            disposition = response.headers["content-disposition"]
            assert history_id not in disposition
            assert re.search(
                rf'filename="battlecard-[a-z0-9-]+-vs-[a-z0-9-]+-\d{{4}}-\d{{2}}-\d{{2}}\.{ext}"',
                disposition,
            )

    def test_export_custom_filename_is_sanitized(self):
        self._post_comparison()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        response = client.get(
            f"/api/v1/history/{history_id}/export.pdf",
            params={"filename": "../Q4 card.md"},
        )
        assert response.headers["content-disposition"] == (
            'attachment; filename="Q4-card.pdf"'
        )

    def test_export_name_endpoint_returns_default_basename(self):
        self._post_comparison()
        history_id = client.get("/api/v1/history").json()["items"][0]["id"]

        response = client.get(f"/api/v1/history/{history_id}/export-name")
        assert response.status_code == 200
        basename = response.json()["basename"]
        assert basename.startswith("battlecard-")
        disposition = client.get(f"/api/v1/history/{history_id}/export.md").headers[
            "content-disposition"
        ]
        assert f'filename="{basename}.md"' in disposition

    def test_export_name_unknown_history_id_returns_404(self):
        response = client.get("/api/v1/history/" + "0" * 32 + "/export-name")
        assert response.status_code == 404

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


class TestCrossOrigin:
    def test_preflight_from_other_origin_is_not_allowed(self):
        response = client.options(
            "/api/v1/history",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        assert "access-control-allow-origin" not in response.headers

    def test_simple_request_gets_no_cors_header(self):
        response = client.get(
            "/api/v1/settings", headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
