"""Render a synthesized battlecard as Markdown or a printable PDF.

Uses reportlab (already a dependency for the sample-doc generator) rather
than adding a templating engine — the layout here is fixed and small enough
that platypus flowables are simpler than a template.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape as _esc

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas import BattlecardResponse

_styles = getSampleStyleSheet()
_H1 = ParagraphStyle("ExportTitle", parent=_styles["Title"], fontSize=18, spaceAfter=10)
_H2 = ParagraphStyle(
    "ExportHeading", parent=_styles["Heading2"], spaceBefore=14, spaceAfter=6
)
_BODY = ParagraphStyle(
    "ExportBody", parent=_styles["BodyText"], fontSize=10, leading=14
)

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0e7ff")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        (
            "ROWBACKGROUNDS",
            (0, 1),
            (-1, -1),
            [colors.white, colors.HexColor("#f8f8f8")],
        ),
    ]
)

_COMPARISON_ROWS = [
    ("Default IP", "default_ip"),
    ("Provisioning Method", "provisioning_method"),
    ("All Provisioning Methods", "provisioning_methods"),
    ("Required Tools", "required_tools"),
    ("Physical Ports", "ports"),
    ("Wi-Fi Standard", "wifi_standard"),
    ("WAN Speed", "wan_speed"),
    ("Form Factor", "form_factor"),
    ("Capabilities", "capabilities"),
]


def _names(battlecard: BattlecardResponse) -> tuple[str, str]:
    model_name = battlecard.comparison_matrix.get("model_name", {})
    return model_name.get("device_a", "Device A"), model_name.get(
        "device_b", "Device B"
    )


def _md_cell(text: str) -> str:
    """Make `text` safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _plain_cell(value) -> str:
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value) if value else "None"
    else:
        text = str(value) if value not in (None, "") else "Unknown"
    return _md_cell(text)


def battlecard_to_markdown(battlecard: BattlecardResponse) -> str:
    cm = battlecard.comparison_matrix
    name_a, name_b = _names(battlecard)
    lines = [
        f"# Battlecard: {name_a} vs {name_b}",
        "",
        "## Installation Friction Index",
        f"- **{name_a}**: {battlecard.installation_friction_score_device_a} / 10",
        f"- **{name_b}**: {battlecard.installation_friction_score_device_b} / 10",
        "",
        "## Competitive Sales Talk Track",
        battlecard.sales_talk_track,
        "",
        "## Side-by-Side Comparison",
        "",
        f"| Attribute | {_md_cell(name_a)} | {_md_cell(name_b)} |",
        "|---|---|---|",
    ]
    for label, key in _COMPARISON_ROWS:
        row = cm.get(key, {})
        cell_a = _plain_cell(row.get("device_a"))
        cell_b = _plain_cell(row.get("device_b"))
        lines.append(f"| {label} | {cell_a} | {cell_b} |")

    lines += ["", "## Competitive Advantages", "", f"### {name_a}"]
    lines += [f"- {adv}" for adv in battlecard.key_advantages_device_a] or [
        "- None identified."
    ]
    lines += ["", f"### {name_b}"]
    lines += [f"- {adv}" for adv in battlecard.key_advantages_device_b] or [
        "- None identified."
    ]

    lines += ["", "## Field-Deployment Risks", "", f"### {name_a}"]
    lines += [f"- {risk}" for risk in battlecard.key_risks_device_a] or [
        "- None identified."
    ]
    lines += ["", f"### {name_b}"]
    lines += [f"- {risk}" for risk in battlecard.key_risks_device_b] or [
        "- None identified."
    ]
    lines.append("")
    return "\n".join(lines)


def battlecard_to_pdf_bytes(battlecard: BattlecardResponse) -> bytes:
    cm = battlecard.comparison_matrix
    name_a, name_b = _names(battlecard)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    def _cell_flowable(value):
        if isinstance(value, list):
            text = "<br/>".join(_esc(str(v)) for v in value) if value else "None"
        else:
            text = _esc(str(value)) if value not in (None, "") else "Unknown"
        return Paragraph(text, _BODY)

    story: list[Flowable] = [
        Paragraph(f"Battlecard: {_esc(name_a)} vs {_esc(name_b)}", _H1),
        Paragraph(
            f"Installation Friction Index &mdash; {_esc(name_a)}: "
            f"{battlecard.installation_friction_score_device_a}/10, "
            f"{_esc(name_b)}: {battlecard.installation_friction_score_device_b}/10",
            _BODY,
        ),
        Paragraph("Competitive Sales Talk Track", _H2),
        Paragraph(_esc(battlecard.sales_talk_track), _BODY),
        Paragraph("Side-by-Side Comparison", _H2),
    ]

    rows = [["Attribute", name_a, name_b]]
    for label, key in _COMPARISON_ROWS:
        row = cm.get(key, {})
        cell_a = _cell_flowable(row.get("device_a"))
        cell_b = _cell_flowable(row.get("device_b"))
        rows.append([label, cell_a, cell_b])
    table = Table(rows, colWidths=[1.4 * inch, 2.8 * inch, 2.8 * inch])
    table.setStyle(_TABLE_STYLE)
    story.append(table)

    advantage_sections = [
        (name_a, battlecard.key_advantages_device_a),
        (name_b, battlecard.key_advantages_device_b),
    ]
    for name, advantages in advantage_sections:
        story.append(Paragraph(f"Competitive Advantages &mdash; {_esc(name)}", _H2))
        if advantages:
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(_esc(a), _BODY)) for a in advantages],  # type: ignore[misc]
                    bulletType="bullet",
                )
            )
        else:
            story.append(Paragraph("None identified.", _BODY))
        story.append(Spacer(1, 4))

    risk_sections = [
        (name_a, battlecard.key_risks_device_a),
        (name_b, battlecard.key_risks_device_b),
    ]
    for name, risks in risk_sections:
        story.append(Paragraph(f"Field-Deployment Risks &mdash; {_esc(name)}", _H2))
        if risks:
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(_esc(r), _BODY)) for r in risks],  # type: ignore[misc]
                    bulletType="bullet",
                )
            )
        else:
            story.append(Paragraph("None identified.", _BODY))
        story.append(Spacer(1, 4))

    doc.build(story)
    return buffer.getvalue()
