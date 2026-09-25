from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

SAMPLE_DOCS_DIR = Path(__file__).parent.parent / "sample_docs"

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


class TestCompareText:
    def _post(self):
        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(encoding="utf-8")
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(encoding="utf-8")
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


class TestComparePdfs:
    def _post(self):
        with open(SAMPLE_DOCS_DIR / "device_a_qsg.pdf", "rb") as a_file, open(
            SAMPLE_DOCS_DIR / "device_b_qsg.pdf", "rb"
        ) as b_file:
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

        device_a_text = (SAMPLE_DOCS_DIR / "device_a_qsg.md").read_text(encoding="utf-8")
        device_b_text = (SAMPLE_DOCS_DIR / "device_b_qsg.md").read_text(encoding="utf-8")
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
