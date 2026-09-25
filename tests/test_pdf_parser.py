import io
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

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


def _build_table_pdf(header: list[str], rows: list[list[str]]) -> bytes:
    """Build PDF bytes containing a genuine bordered ReportLab Table."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER)
    table = Table([header] + rows)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    doc.build([table])
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
        pdf_bytes = _build_pdf([["Page one content"], ["Page two content"]])
        text = extract_text_from_pdf(pdf_bytes)
        assert text.index("Page one content") < text.index("Page two content")

    def test_boilerplate_matching_is_case_insensitive(self):
        pdf_bytes = _build_pdf(
            [["fcc compliance notice here.", "Real content line."]]
        )
        text = extract_text_from_pdf(pdf_bytes)
        assert "fcc compliance" not in text.lower()
        assert "Real content line." in text


class TestTableExtraction:
    def test_table_survives_extraction_as_markdown_table(self):
        pdf_bytes = _build_table_pdf(
            ["LED", "Meaning"],
            [
                ["Power", "solid green once online"],
                ["Alert", "solid red on fault"],
            ],
        )
        text = extract_text_from_pdf(pdf_bytes)

        # A markdown table has a header row and a "|---|---|"-style
        # separator row - not just pipe characters floating in plain text.
        assert re.search(r"\|\s*-{2,}\s*\|", text)
        table_lines = [line for line in text.splitlines() if "|" in line]
        assert any("LED" in line and "Meaning" in line for line in table_lines)
        assert any(
            "Power" in line and "solid green once online" in line
            for line in table_lines
        )
        assert any(
            "Alert" in line and "solid red on fault" in line for line in table_lines
        )

    def test_multi_column_table_preserves_all_columns(self):
        pdf_bytes = _build_table_pdf(
            ["Port", "Type", "Notes"],
            [
                ["PON", "SC/APC fiber", "Connects to ONT"],
                ["LAN1-4", "RJ45 Gigabit Ethernet", "Auto-negotiating"],
                ["Power", "DC barrel, 12V", "Included adapter only"],
            ],
        )
        text = extract_text_from_pdf(pdf_bytes)
        table_lines = [line for line in text.splitlines() if "|" in line]
        assert any(
            "Port" in line and "Type" in line and "Notes" in line
            for line in table_lines
        )
        assert any(
            "PON" in line and "SC/APC fiber" in line and "Connects to ONT" in line
            for line in table_lines
        )
