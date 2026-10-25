"""Render a synthesized battlecard as Markdown or a printable PDF.

Uses reportlab (already a dependency for the sample-doc generator) rather
than adding a templating engine — the layout here is fixed and small enough
that platypus flowables are simpler than a template.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from typing import Optional
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

from qsg_synthesizer.schemas import BattlecardResponse

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
# Only the LLM fills these, so they're shown only when either side has one
# (never for heuristic-only comparisons or records saved before them).
_SETUP_ROWS = [
    ("Setup Steps", "step_count"),
    ("Setup Difficulty", "setup_difficulty"),
    ("Setup Strengths", "setup_strengths"),
    ("Setup Gaps", "setup_gaps"),
]


def _comparison_rows(cm: dict) -> list[tuple[str, str]]:
    def present(key: str) -> bool:
        values = cm.get(key) or {}
        return any(
            values.get(d) not in (None, "", []) for d in ("device_a", "device_b")
        )

    # Records saved before extractors were named have no "Read With" row.
    read_with = [("Read With", "extracted_with")] if present("extracted_with") else []
    return read_with + _COMPARISON_ROWS + [r for r in _SETUP_ROWS if present(r[1])]


def _names(battlecard: BattlecardResponse) -> tuple[str, str]:
    model_name = battlecard.comparison_matrix.get("model_name", {})
    return model_name.get("device_a", "Device A"), model_name.get(
        "device_b", "Device B"
    )


_SLUG_MAX_CHARS = 40
_FILENAME_MAX_CHARS = 100
_PATH_SEPARATOR_RE = re.compile(r"[\\/]+")
_UNSAFE_FILENAME_CHARS_RE = re.compile(r"[^A-Za-z0-9._()-]+")
_EXPORT_EXTENSIONS = (".markdown", ".md", ".pdf")


def _to_ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _to_ascii(text).lower()).strip("-")
    return slug[:_SLUG_MAX_CHARS].strip("-") or fallback


def default_export_basename(
    battlecard: BattlecardResponse, created_at: datetime
) -> str:
    """Default download name `battlecard-<a>-vs-<b>-<date>`, without extension."""
    name_a, name_b = _names(battlecard)
    return (
        f"battlecard-{_slug(name_a, 'device-a')}-vs-{_slug(name_b, 'device-b')}"
        f"-{created_at:%Y-%m-%d}"
    )


def sanitize_export_filename(
    requested: Optional[str], ext: str, default_basename: str
) -> str:
    """Turn a user-supplied name into a safe download name ending in `.ext`.

    The result is ASCII-only and limited to letters, digits and `._()-`, so it
    can sit inside a Content-Disposition header without escaping and can never
    carry a path. Anything unusable falls back to `default_basename`.
    """
    parts = [p for p in _PATH_SEPARATOR_RE.split(requested or "") if p.strip()]
    name = _to_ascii(parts[-1]) if parts else ""
    name = _UNSAFE_FILENAME_CHARS_RE.sub("-", name.strip())
    while name.lower().endswith(_EXPORT_EXTENSIONS):
        name = name[: name.rfind(".")]
    name = re.sub(r"-{2,}", "-", name).strip(".-")[:_FILENAME_MAX_CHARS].strip(".-")
    return f"{name or default_basename}.{ext}"


def _labels(battlecard: BattlecardResponse) -> tuple[str, str]:
    """Display names for headings and tables; adds the role in a sales comparison."""
    name_a, name_b = _names(battlecard)
    if battlecard.perspective == "sales":
        return f"{name_a} (ours)", f"{name_b} (competitor)"
    return name_a, name_b


FRICTION_NOT_COMPARED = (
    "Not compared: install effort is only scored when both documents are "
    "quick start guides."
)


def _friction_scored(battlecard: BattlecardResponse) -> bool:
    return (
        battlecard.installation_friction_score_device_a is not None
        and battlecard.installation_friction_score_device_b is not None
    )


def _extra_spec_rows(cm: dict) -> list[tuple[str, Optional[str], Optional[str]]]:
    return [
        (str(row.get("name", "")), row.get("device_a"), row.get("device_b"))
        for row in cm.get("extra_specs") or []
    ]


def _spec_cell(value: Optional[str]) -> str:
    return _md_cell(value) if value else "Not listed"


def _md_cell(text: str) -> str:
    """Make `text` safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _matrix_value(cm: dict, key: str, device: str):
    """A comparison-matrix value for export. When the document listed no
    provisioning method, the primary one is only the scoring fallback."""
    if (
        key == "provisioning_method"
        and cm.get("provisioning_methods", {}).get(device) == []
    ):
        return "Not specified"
    value = cm.get(key, {}).get(device)
    if key == "setup_difficulty" and value:
        return str(value).capitalize()
    return value


def _plain_cell(value) -> str:
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value) if value else "None"
    else:
        text = str(value) if value not in (None, "") else "Unknown"
    return _md_cell(text)


def battlecard_to_markdown(battlecard: BattlecardResponse) -> str:
    cm = battlecard.comparison_matrix
    name_a, name_b = _names(battlecard)
    label_a, label_b = _labels(battlecard)
    lines = [
        f"# Battlecard: {name_a} vs {name_b}",
        "",
        "## Installation Friction Index",
        *(
            [
                f"- **{label_a}**: "
                f"{battlecard.installation_friction_score_device_a} / 10",
                f"- **{label_b}**: "
                f"{battlecard.installation_friction_score_device_b} / 10",
            ]
            if _friction_scored(battlecard)
            else [FRICTION_NOT_COMPARED]
        ),
        "",
        "## Competitive Sales Talk Track",
        battlecard.sales_talk_track,
        "",
        "## Side-by-Side Comparison",
        "",
        f"| Attribute | {_md_cell(label_a)} | {_md_cell(label_b)} |",
        "|---|---|---|",
    ]
    for label, key in _comparison_rows(cm):
        cell_a = _plain_cell(_matrix_value(cm, key, "device_a"))
        cell_b = _plain_cell(_matrix_value(cm, key, "device_b"))
        lines.append(f"| {label} | {cell_a} | {cell_b} |")
    for spec_name, value_a, value_b in _extra_spec_rows(cm):
        lines.append(
            f"| {_md_cell(spec_name)} | {_spec_cell(value_a)} | {_spec_cell(value_b)} |"
        )

    lines += ["", "## Competitive Advantages", "", f"### {label_a}"]
    lines += [f"- {adv}" for adv in battlecard.key_advantages_device_a] or [
        "- None identified."
    ]
    lines += ["", f"### {label_b}"]
    lines += [f"- {adv}" for adv in battlecard.key_advantages_device_b] or [
        "- None identified."
    ]

    lines += ["", "## Field-Deployment Risks", "", f"### {label_a}"]
    lines += [f"- {risk}" for risk in battlecard.key_risks_device_a] or [
        "- None identified."
    ]
    lines += ["", f"### {label_b}"]
    lines += [f"- {risk}" for risk in battlecard.key_risks_device_b] or [
        "- None identified."
    ]
    if battlecard.warnings:
        lines += ["", "## Notes", ""]
        lines += [f"- {warning}" for warning in battlecard.warnings]
    lines.append("")
    return "\n".join(lines)


def battlecard_to_pdf_bytes(battlecard: BattlecardResponse) -> bytes:
    cm = battlecard.comparison_matrix
    name_a, name_b = _names(battlecard)
    label_a, label_b = _labels(battlecard)

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
            (
                (
                    f"Installation Friction Index &mdash; {_esc(label_a)}: "
                    f"{battlecard.installation_friction_score_device_a}/10, "
                    f"{_esc(label_b)}: "
                    f"{battlecard.installation_friction_score_device_b}/10"
                )
                if _friction_scored(battlecard)
                else "Installation Friction Index &mdash; "
                + _esc(FRICTION_NOT_COMPARED)
            ),
            _BODY,
        ),
        Paragraph("Competitive Sales Talk Track", _H2),
        Paragraph(_esc(battlecard.sales_talk_track), _BODY),
        Paragraph("Side-by-Side Comparison", _H2),
    ]

    rows: list[list] = [["Attribute", label_a, label_b]]
    for label, key in _comparison_rows(cm):
        cell_a = _cell_flowable(_matrix_value(cm, key, "device_a"))
        cell_b = _cell_flowable(_matrix_value(cm, key, "device_b"))
        rows.append([label, cell_a, cell_b])
    for spec_name, value_a, value_b in _extra_spec_rows(cm):
        rows.append(
            [
                Paragraph(_esc(spec_name), _BODY),
                Paragraph(_esc(value_a or "Not listed"), _BODY),
                Paragraph(_esc(value_b or "Not listed"), _BODY),
            ]
        )
    table = Table(rows, colWidths=[1.4 * inch, 2.8 * inch, 2.8 * inch])
    table.setStyle(_TABLE_STYLE)
    story.append(table)

    advantage_sections = [
        (label_a, battlecard.key_advantages_device_a),
        (label_b, battlecard.key_advantages_device_b),
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
        (label_a, battlecard.key_risks_device_a),
        (label_b, battlecard.key_risks_device_b),
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

    if battlecard.warnings:
        story.append(Paragraph("Notes", _H2))
        story.append(
            ListFlowable(
                [ListItem(Paragraph(_esc(w), _BODY)) for w in battlecard.warnings],  # type: ignore[misc]
                bulletType="bullet",
            )
        )

    doc.build(story)
    return buffer.getvalue()
