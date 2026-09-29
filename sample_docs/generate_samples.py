"""Generate realistic mock PDF QSGs for exercising app/tools/pdf_parser.py.

Uses genuine ReportLab layout constructs — a two-column instruction section
via real page-template Frames, and a bordered grid Table for ports/LED
indicators — rather than flat text lines, so pymupdf4llm's layout-aware
table detection has real visual structure to reconstruct into markdown
tables. Run directly: `python sample_docs/generate_samples.py`.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

SAMPLE_DOCS_DIR = Path(__file__).parent

FCC_NOTICE = "FCC Compliance: This device complies with Part 15 of the FCC Rules."
COPYRIGHT_NOTICE = "© 2026 Example Corp. All Rights Reserved."

_MARGIN = 0.75 * inch
_BOTTOM_MARGIN = 0.85 * inch
_COLUMN_GAP = 0.3 * inch
_PAGE_WIDTH, _PAGE_HEIGHT = LETTER

_styles = getSampleStyleSheet()
_H1 = ParagraphStyle("QSGTitle", parent=_styles["Title"], fontSize=16, spaceAfter=12)
_H2 = ParagraphStyle(
    "QSGHeading", parent=_styles["Heading2"], spaceBefore=10, spaceAfter=6
)
_BODY = ParagraphStyle("QSGBody", parent=_styles["BodyText"], fontSize=9.5, leading=13)
_BULLET = ParagraphStyle("QSGBullet", parent=_BODY, leftIndent=12)

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        (
            "ROWBACKGROUNDS",
            (0, 1),
            (-1, -1),
            [colors.white, colors.HexColor("#f5f5f5")],
        ),
    ]
)


class _NumberedCanvas(Canvas):
    """Canvas that stamps a 'Page X of Y' footer once the page count is known."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total_pages)
            super().showPage()
        super().save()

    def _draw_footer(self, total_pages: int) -> None:
        self.setFont("Helvetica", 8)
        self.drawCentredString(
            _PAGE_WIDTH / 2, 0.5 * inch, f"Page {self.getPageNumber()} of {total_pages}"
        )
        self.drawString(_MARGIN, 0.35 * inch, FCC_NOTICE)
        self.drawString(_MARGIN, 0.2 * inch, COPYRIGHT_NOTICE)


def _two_column_frames() -> list[Frame]:
    column_width = (_PAGE_WIDTH - 2 * _MARGIN - _COLUMN_GAP) / 2
    top = _PAGE_HEIGHT - _MARGIN
    height = top - _BOTTOM_MARGIN
    left = Frame(_MARGIN, _BOTTOM_MARGIN, column_width, height, id="col1")
    right = Frame(
        _MARGIN + column_width + _COLUMN_GAP,
        _BOTTOM_MARGIN,
        column_width,
        height,
        id="col2",
    )
    return [left, right]


def _one_column_frame() -> Frame:
    width = _PAGE_WIDTH - 2 * _MARGIN
    top = _PAGE_HEIGHT - _MARGIN
    height = top - _BOTTOM_MARGIN
    return Frame(_MARGIN, _BOTTOM_MARGIN, width, height, id="full")


def _bullets(items: list[str], bullet_type: str = "bullet") -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(item, _BULLET)) for item in items],
        bulletType=bullet_type,
        leftIndent=14,
    )


def _grid_table(header: list[str], rows: list[tuple], col_widths: list[float]) -> Table:
    data = [header] + [list(row) for row in rows]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    return table


def _build_pdf(spec: dict, pdf_path: Path) -> None:
    doc = BaseDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="TwoColumn", frames=_two_column_frames()),
            PageTemplate(id="OneColumn", frames=[_one_column_frame()]),
        ]
    )

    story = [
        Paragraph(spec["title"], _H1),
        Paragraph("Overview", _H2),
        Paragraph(spec["overview"], _BODY),
        Paragraph("What's in the Box", _H2),
        _bullets(spec["box_items"]),
        Spacer(1, 6),
        FrameBreak(),
        Paragraph("Provisioning", _H2),
        Paragraph(spec["provisioning_intro"], _BODY),
        _bullets(spec["provisioning_bullets"]),
        Paragraph(spec["provisioning_outro"], _BODY),
    ]

    if spec.get("required_tools"):
        story += [Paragraph("Required Tools", _H2), _bullets(spec["required_tools"])]
    else:
        story += [
            Paragraph("Required Tools", _H2),
            Paragraph(spec["required_tools_note"], _BODY),
        ]

    story += [
        Paragraph("Key Features", _H2),
        _bullets(spec["key_features"]),
        Paragraph("Specifications", _H2),
        _bullets(spec["specifications"]),
    ]

    story += [
        NextPageTemplate("OneColumn"),
        PageBreak(),
        Paragraph("Cabling & Ports", _H2),
        _grid_table(
            ["Port", "Type", "Notes"],
            spec["ports"],
            [1.3 * inch, 2.0 * inch, 2.7 * inch],
        ),
        Spacer(1, 12),
        Paragraph("LED Indicators", _H2),
        _grid_table(
            ["LED", "Meaning"],
            spec["leds"],
            [1.6 * inch, 4.4 * inch],
        ),
        Spacer(1, 12),
        Paragraph("Installation Steps", _H2),
        _bullets(spec["installation_steps"], bullet_type="1"),
    ]

    if spec.get("closing_note"):
        story += [Spacer(1, 6), Paragraph(spec["closing_note"], _BODY)]

    doc.build(story, canvasmaker=_NumberedCanvas)


DEVICE_A = {
    "title": "NovaFiber GX400 — Quick Start Guide",
    "overview": (
        "The NovaFiber GX400 is a carrier-grade XGS-PON fiber gateway designed "
        "for zero-touch field deployment. Most installs require no on-site "
        "configuration."
    ),
    "box_items": [
        "NovaFiber GX400 unit",
        "Fiber patch cord (SC/APC)",
        "Power adapter",
        "Quick Start card with QR code",
    ],
    "provisioning_intro": "The GX400 auto-provisions on power-up:",
    "provisioning_bullets": [
        "<b>TR-069 (CWMP)</b>: The unit calls home to the carrier ACS "
        "automatically once fiber and power are connected. All WAN, VoIP, and "
        "Wi-Fi profiles are pushed remotely — no technician configuration "
        "required.",
        "<b>DHCP</b>: LAN-side clients receive addresses automatically from "
        "the gateway's built-in DHCP server.",
        "<b>QR / Mobile App</b>: Installers (or end customers) may scan the "
        "QR code printed on the unit's label with the NovaFiber Connect app "
        "to confirm activation status and run a guided self-install flow.",
        "<b>Web GUI</b>: An advanced local web interface is available at "
        "192.168.10.1 for technicians who need to override auto-provisioned "
        "settings, but it is not required for a standard install.",
    ],
    "provisioning_outro": (
        "No console port is present on this model; there is no local CLI access."
    ),
    "required_tools": [],
    "required_tools_note": (
        "No additional tools are required for a standard install. A "
        "smartphone camera is used to scan the setup QR code."
    ),
    "key_features": [
        "Wi-Fi 6 (802.11ax) tri-band with mesh support",
        "Built-in ONT — no separate ONT box needed",
        "USB 3.0 port for external storage or printer sharing",
        "Automatic firmware updates with zero-touch rollback",
    ],
    "specifications": [
        "Wi-Fi Standard: 802.11ax (Wi-Fi 6), tri-band",
        "WAN Speed: Up to 2.5 Gbps (XGS-PON)",
        "Form Factor: Compact desktop, wall-mountable",
    ],
    "ports": [
        ("PON", "SC/APC fiber", "Connects to carrier ONT drop cable"),
        ("LAN1–LAN4", "RJ45 Gigabit Ethernet", "Auto-negotiating"),
        ("Wi-Fi", "802.11ax (internal)", "No external antenna cabling"),
        ("Power", "DC barrel, 12V", "Included adapter only"),
    ],
    "leds": [
        ("Power/Status", "pulsing green during startup and ACS handshake"),
        ("Power/Status", "solid green once fully online and provisioned"),
        ("PON", "blinking amber 2Hz while acquiring optical signal"),
        ("PON", "solid green once optical link is established"),
        ("Alert", "solid red if optical signal is lost or provisioning fails"),
    ],
    "installation_steps": [
        "Connect the fiber patch cord to the PON port.",
        "Connect power.",
        "Wait for the Power/Status LED to turn solid green (typically under "
        "3 minutes).",
        "Optionally scan the QR code with the NovaFiber Connect app to "
        "verify service.",
    ],
    "closing_note": (
        "No screwdriver, console cable, or laptop is required for a standard "
        "residential or SMB install."
    ),
}

DEVICE_B = {
    "title": "LegacyLink RG-210 — Quick Start Guide",
    "overview": (
        "The LegacyLink RG-210 is a DSL/Ethernet gateway. Installation "
        "requires a technician to manually configure WAN settings via the "
        "local console or web interface; the unit does not support remote "
        "auto-provisioning."
    ),
    "box_items": [
        "LegacyLink RG-210 unit",
        "RJ11 DSL cable",
        "RJ45 Ethernet cable",
        "Power adapter",
        "RS-232-to-USB console cable",
    ],
    "provisioning_intro": "",
    "provisioning_bullets": [
        "<b>Console</b>: Initial setup is performed over the RS-232 console "
        "port at 9600 baud using a terminal emulator. Technicians log in "
        "with the default credentials printed on the bottom label and "
        "manually enter WAN credentials.",
        "<b>Web GUI</b>: Once the unit is powered on, a local configuration "
        "page is reachable at 192.168.1.1 (default static management IP) "
        "for further configuration after initial console setup.",
        "<b>Other</b>: WAN connection mode (PPPoE vs. static) is selected "
        "via a physical 4-position DIP switch bank on the rear panel before "
        "power-on. Incorrect switch settings will prevent WAN negotiation.",
    ],
    "provisioning_outro": "DHCP and TR-069 are not supported on this model.",
    "required_tools": [
        "Phillips (#2) screwdriver, to remove the rear panel and set "
        "internal jumpers",
        "RS-232-to-USB console cable (included) and a laptop with a "
        "terminal emulator (e.g. PuTTY, minicom) for initial configuration",
        "Small flathead screwdriver, to set the DIP switch bank for WAN "
        "mode (PPPoE vs. static IP)",
    ],
    "key_features": [
        "2x FXS ports for analog phone service (VoIP)",
        "Built-in battery backup connector for lifeline phone service",
        "Parental controls via MAC address filtering",
    ],
    "specifications": [
        "Wi-Fi Standard: 802.11ac (Wi-Fi 5), dual-band",
        "WAN Speed: Up to 100 Mbps (ADSL2+/VDSL2)",
        "Form Factor: Desktop, not wall-mountable",
    ],
    "ports": [
        ("DSL", "RJ11", "Connects to telco NID"),
        ("LAN1–LAN4", "RJ45 Fast Ethernet", "Manual VLAN jumpers on rear panel"),
        ("Console", "RS-232 (RJ45-to-DB9)", "Requires included adapter cable"),
        ("Power", "DC barrel, 12V", "Included adapter only"),
    ],
    "leds": [
        ("Power", "solid green once the unit finishes its boot self-test"),
        ("Power", "blinking green during boot self-test"),
        ("DSL", "blinking amber while training/syncing with the DSLAM"),
        ("DSL", "solid green once DSL sync is achieved"),
        ("Status", "solid red if DSL sync fails after 3 attempts"),
        ("Status", "blinking red if an internal self-test fails"),
    ],
    "installation_steps": [
        "Power off the unit before removing the rear panel.",
        "Set the WAN mode DIP switches per the table in Appendix B.",
        "Reattach the rear panel and connect the DSL and power cables.",
        "Connect the console cable to a laptop and open a terminal session "
        "at 9600 baud.",
        "Log in with the default console credentials and enter WAN account "
        "credentials as provided by the carrier.",
        "Verify the DSL LED reaches solid green before disconnecting the "
        "console cable.",
        "Optionally browse to 192.168.1.1 to configure Wi-Fi and LAN " "settings.",
    ],
    "closing_note": None,
}


def main() -> None:
    _build_pdf(DEVICE_A, SAMPLE_DOCS_DIR / "device_a_qsg.pdf")
    _build_pdf(DEVICE_B, SAMPLE_DOCS_DIR / "device_b_qsg.pdf")


if __name__ == "__main__":
    main()
