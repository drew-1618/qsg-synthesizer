import io

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen.canvas import Canvas

from app.tools.pdf_parser import extract_text_from_pdf


def _build_pdf(pages: list[list[str]]) -> bytes:
    """Build minimal PDF bytes with one drawString per line, per page."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=LETTER)
    _, height = LETTER
    for page_lines in pages:
        y = height - 72
        canvas.setFont("Helvetica", 10)
        for line in page_lines:
            canvas.drawString(72, y, line)
            y -= 14
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


class TestExtractTextFromPdf:
    def test_extracts_plain_content_lines(self):
        pdf_bytes = _build_pdf([["Default IP: 192.168.1.1", "Install the unit."]])
        text = extract_text_from_pdf(pdf_bytes)
        assert "Default IP: 192.168.1.1" in text
        assert "Install the unit." in text

    def test_strips_fcc_compliance_line(self):
        pdf_bytes = _build_pdf(
            [
                [
                    "Default IP: 192.168.1.1",
                    "FCC Compliance: This device complies with Part 15.",
                ]
            ]
        )
        text = extract_text_from_pdf(pdf_bytes)
        assert "FCC Compliance" not in text
        assert "Default IP: 192.168.1.1" in text

    def test_strips_all_rights_reserved_line(self):
        pdf_bytes = _build_pdf(
            [["Setup Guide", "© 2026 Example Corp. All Rights Reserved."]]
        )
        text = extract_text_from_pdf(pdf_bytes)
        assert "All Rights Reserved" not in text
        assert "Setup Guide" in text

    def test_strips_page_x_of_y_line(self):
        pdf_bytes = _build_pdf([["Cabling & Ports", "Page 1 of 3"]])
        text = extract_text_from_pdf(pdf_bytes)
        assert "Page 1 of 3" not in text
        assert "Cabling & Ports" in text

    def test_collapses_excessive_blank_lines(self):
        pdf_bytes = _build_pdf([["First line", "", "", "", "Second line"]])
        text = extract_text_from_pdf(pdf_bytes)
        assert "\n\n\n" not in text
        assert "First line" in text
        assert "Second line" in text

    def test_multi_page_pdf_concatenates_text_in_order(self):
        pdf_bytes = _build_pdf(
            [["Page one content"], ["Page two content"]]
        )
        text = extract_text_from_pdf(pdf_bytes)
        assert text.index("Page one content") < text.index("Page two content")

    def test_boilerplate_matching_is_case_insensitive(self):
        pdf_bytes = _build_pdf(
            [["fcc compliance notice here", "Real content line"]]
        )
        text = extract_text_from_pdf(pdf_bytes)
        assert "fcc compliance" not in text.lower()
        assert "Real content line" in text
