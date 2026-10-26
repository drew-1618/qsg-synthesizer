"""The built-in parser on documents it wasn't written against.

`tests/fixtures/realistic/` holds vendor-style quick start guides and
datasheets (fictional devices) laid out the way real ones are: other heading
names, combined hardware tables, one column per model, PDF-style bold
headings, plain pasted text. The sample docs are pinned here too, so a
parser change that shifts what they extract shows up as a deliberate edit.
"""

from pathlib import Path

import pytest

from qsg_synthesizer.services import orchestrator
from qsg_synthesizer.services.orchestrator import _heuristic_extract_qsg
from qsg_synthesizer.tools.pdf_parser import extract_text_from_pdf

ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "realistic"
FIXTURES = sorted(path.name for path in FIXTURES_DIR.iterdir())


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(orchestrator, "_select_llm_provider", lambda: None)


def _sample_text(name: str) -> str:
    path = ROOT / "sample_docs" / name
    if name.endswith(".pdf"):
        return extract_text_from_pdf(path.read_bytes())
    return path.read_text(encoding="utf-8")


def _fixture(name: str):
    text = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    return _heuristic_extract_qsg(text, name)


# name: (device, default IP, methods, tools, ports, LEDs, document type, specs)
SAMPLE_SNAPSHOT = {
    "device_a_qsg.md": (
        "NovaFiber GX400",
        "192.168.10.1",
        ["QR/Mobile App", "TR-069", "DHCP", "Web GUI", "Console"],
        0,
        4,
        5,
        "quick_start_guide",
        3,
    ),
    "device_a_qsg.pdf": (
        "NovaFiber GX400",
        "192.168.10.1",
        ["QR/Mobile App", "TR-069", "Web GUI", "Console"],
        0,
        4,
        5,
        "quick_start_guide",
        3,
    ),
    "device_b_qsg.md": (
        "LegacyLink RG-210",
        "192.168.1.1",
        ["Console", "Other", "Web GUI"],
        3,
        4,
        6,
        "quick_start_guide",
        3,
    ),
    "device_b_qsg.pdf": (
        "LegacyLink RG-210",
        "192.168.1.1",
        ["Console", "Web GUI", "Other"],
        3,
        4,
        6,
        "quick_start_guide",
        3,
    ),
    "device_c_datasheet.md": ("Corvane AX9000", None, [], 0, 0, 0, "datasheet", 9),
    "device_d_datasheet.md": ("Halden FX-600", None, [], 0, 0, 0, "datasheet", 7),
}


@pytest.mark.parametrize("name", sorted(SAMPLE_SNAPSHOT))
def test_sample_docs_extract_as_pinned(name):
    e = _heuristic_extract_qsg(_sample_text(name), name)
    assert (
        e.device_name,
        e.default_ip,
        [m.value for m in e.provisioning_methods],
        len(e.required_tools),
        len(e.cabling_ports),
        len(e.led_indicators),
        e.document_type.value if e.document_type else None,
        len(e.specs),
    ) == SAMPLE_SNAPSHOT[name]


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_extracts_without_error(name):
    extraction = _fixture(name)
    assert extraction.device_name
    assert extraction.source_document == name


class TestSections:
    @pytest.mark.parametrize(
        "heading, section",
        [
            ("## What's in the Box", "box"),
            ("### Package Contents", "box"),
            ("## What you'll need", "tools"),
            ("**Before You Begin**", "tools"),
            ("## 2. Required Tools:", "tools"),
            ("## Cabling & Ports", "ports"),
            ("## Interfaces", "ports"),
            ("## Rear panel", "ports"),
            ("## LED Indicators", "leds"),
            ("## **LED Status**", "leds"),
            ("## Status lights", "leds"),
            ("### Hardware Overview", "hardware"),
            ("## Technical Specifications", "specs"),
            ("## Physical and environmental", "specs"),
            ("## Product highlights", "features"),
            ("## Installation Steps", "steps"),
            ("### Setting up your router", "steps"),
            ("## Mounting the access point", "steps"),
            ("## Initial configuration", "steps"),
            ("## Ordering information", "ordering"),
        ],
    )
    def test_heading_wordings_map_to_sections(self, heading, section):
        sections = orchestrator._split_sections(f"# Device\n\n{heading}\n\nbody\n")
        assert sections[section] == ["", "body"]

    def test_plain_text_headings_are_recognized(self):
        text = "Acme Z1\n\nWHAT YOU NEED\n- Laptop\n\nInstallation\n1. Plug in.\n"
        sections = orchestrator._split_sections(text)
        assert orchestrator._parse_bullets(sections["tools"]) == ["Laptop"]
        assert "1. Plug in." in sections["steps"]

    def test_a_plain_line_that_reads_like_a_step_is_not_a_heading(self):
        text = "## Installation\n\nConnect the WAN cable\nPower on\n"
        sections = orchestrator._split_sections(text)
        assert sections["steps"] == ["", "Connect the WAN cable", "Power on"]

    def test_an_unknown_heading_does_not_continue_the_previous_section(self):
        text = "# Acme Z1\n\n## Required Tools\n- Laptop\n\n## Warranty\n- Two years\n"
        sections = orchestrator._split_sections(text)
        assert orchestrator._parse_bullets(sections["tools"]) == ["Laptop"]

    def test_unknown_subheadings_stay_in_their_section(self):
        text = "## Installation\n\n### Step 1: Mount\n1. Drill.\n"
        sections = orchestrator._split_sections(text)
        assert sections["steps"] == ["", "### Step 1: Mount", "1. Drill."]

    def test_subheadings_of_specifications_stay_specifications(self):
        text = "## Specifications\n\n### Interfaces\n- WAN: 10G\n"
        sections = orchestrator._split_sections(text)
        assert "ports" not in sections
        assert "- WAN: 10G" in sections["specs"]

    @pytest.mark.parametrize(
        "name, tools",
        [
            ("ap_qsg.md", 4),
            ("ont_qsg_pdf.md", 3),
            ("modem_qsg.txt", 2),
        ],
    )
    def test_tool_lists_under_other_headings_are_found(self, name, tools):
        assert len(_fixture(name).required_tools) == tools

    def test_datasheet_spec_headings_are_found(self):
        extraction = _fixture("switch_datasheet.md")
        assert extraction.document_type.value == "datasheet"
        assert "PoE budget" in {s.name for s in extraction.specs}
