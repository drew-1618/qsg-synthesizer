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
from qsg_synthesizer.services.orchestrator import (
    _heuristic_extract,
    _heuristic_extract_qsg,
    _normalize_to_device_spec,
)
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


# name: (device, default IP, methods, tools, ports, LEDs, document type,
#        open-ended spec rows shown)
SAMPLE_SNAPSHOT = {
    "device_a_qsg.md": (
        "NovaFiber GX400",
        "192.168.10.1",
        ["QR/Mobile App", "TR-069", "DHCP", "Web GUI", "Console"],
        0,
        4,
        5,
        "quick_start_guide",
        0,
    ),
    "device_a_qsg.pdf": (
        "NovaFiber GX400",
        "192.168.10.1",
        ["QR/Mobile App", "TR-069", "Web GUI", "Console"],
        0,
        4,
        5,
        "quick_start_guide",
        0,
    ),
    "device_b_qsg.md": (
        "LegacyLink RG-210",
        "192.168.1.1",
        ["Console", "Other", "Web GUI"],
        3,
        4,
        6,
        "quick_start_guide",
        0,
    ),
    "device_b_qsg.pdf": (
        "LegacyLink RG-210",
        "192.168.1.1",
        ["Console", "Web GUI", "Other"],
        3,
        4,
        6,
        "quick_start_guide",
        0,
    ),
    "device_c_datasheet.md": ("Corvane AX9000", None, [], 0, 0, 0, "datasheet", 6),
    "device_d_datasheet.md": ("Halden FX-600", None, [], 0, 0, 0, "datasheet", 4),
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
        len(_normalize_to_device_spec(e).specs),
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
        assert {"PoE budget", "Weight"} <= {s.name for s in extraction.specs}


class TestTables:
    def test_combined_hardware_table_yields_ports_and_split_led_states(self):
        extraction = _fixture("router_qsg.md")
        assert [p.port_name for p in extraction.cabling_ports] == [
            "WAN",
            "LAN 1~4",
            "Power (DC-IN)",
        ]
        assert extraction.cabling_ports[2].port_type == "DC-IN"
        power = [led for led in extraction.led_indicators if led.led_name == "Power"]
        assert [led.raw_description for led in power] == [
            "Off: No power",
            "On: Device is ready",
            "Flashing slow: Rescue mode",
        ]

    def test_three_column_led_table_keeps_state_and_continues_blank_names(self):
        extraction = _fixture("ont_qsg_pdf.md")
        leds = [
            (led.led_name, led.raw_description) for led in extraction.led_indicators
        ]
        assert leds[:2] == [
            ("PWR", "Solid green: Power is on"),
            ("PWR", "Off: No power"),
        ]
        spec = _normalize_to_device_spec(extraction)
        assert [led.state for led in spec.led_states][:1] == ["ONLINE"]

    def test_port_columns_are_found_by_header(self):
        ports = _fixture("ont_qsg_pdf.md").cabling_ports
        assert (ports[0].port_name, ports[0].port_type, ports[0].notes) == (
            "PON",
            "SC/APC",
            "Connects to the fiber drop",
        )
        assert "4x GE (RJ45) - Gigabit Ethernet to customer equipment" in (
            _normalize_to_device_spec(_fixture("ont_qsg_pdf.md")).ports
        )

    def test_port_and_led_bullets(self):
        extraction = _fixture("ap_qsg.md")
        assert extraction.cabling_ports[0].port_name == "ETH0/PoE"
        assert extraction.cabling_ports[0].port_type == "2.5GBASE-T RJ45, PoE+ input"
        text = "# Z1\n\n## LEDs\n\n- Power: solid green when ready\n"
        assert _heuristic_extract_qsg(text, "z").led_indicators[0].led_name == "Power"

    def test_a_table_is_classified_by_its_own_header(self):
        text = (
            "# Z1\n\n## Front and back\n\n| LED | Meaning |\n|---|---|\n"
            "| Power | solid green |\n"
        )
        assert len(_heuristic_extract_qsg(text, "z").led_indicators) == 1

    def test_multi_model_table_reads_the_first_model_with_a_note(self):
        extraction, notes = _heuristic_extract(
            (FIXTURES_DIR / "switch_datasheet.md").read_text(), "s"
        )
        specs = {s.name: s.value for s in extraction.specs}
        assert specs["PoE budget"] == "370 W"
        assert "Model" not in specs
        assert notes == [
            "The specifications table covers 2 models (EL-5424P, EL-5448P); "
            "the built-in parser read the first, EL-5424P."
        ]

    def test_multi_model_table_reads_the_model_the_title_names(self):
        text = (
            "# Acme X2 Datasheet\n\n## Specifications\n\n| Model | X1 | X2 |\n"
            "|---|---|---|\n| Power | 10 W | 20 W |\n"
        )
        extraction, notes = _heuristic_extract(text, "x")
        assert extraction.specs[0].value == "20 W"
        assert notes == []

    def test_value_and_notes_columns_are_not_models(self):
        text = (
            "# Acme Datasheet\n\n## Specifications\n\n| Spec | Value | Notes |\n"
            "|---|---|---|\n| Power | 10 W | typical |\n"
        )
        extraction, notes = _heuristic_extract(text, "x")
        assert extraction.specs[0].value == "10 W"
        assert notes == []

    def test_label_value_lines_and_bold_labels_are_specs(self):
        extraction = _fixture("gateway_datasheet.md")
        specs = {s.name: s.value for s in extraction.specs}
        assert specs["Weight"] == "1.1 kg"
        assert specs["WAN"] == "1 x 10G SFP+ and 1 x 10GBASE-T"
        assert extraction.wifi_standard == "IEEE 802.11be (Wi-Fi 7)"

    def test_fixed_field_synonyms_need_a_value_that_fits(self):
        router = _fixture("router_qsg.md")
        assert router.wifi_standard == "IEEE 802.11a/b/g/n/ac/ax"
        text = "# Z1 Datasheet\n\n## Specifications\n\n- Wireless: optional module\n"
        extraction = _heuristic_extract_qsg(text, "z")
        assert extraction.wifi_standard is None
        assert extraction.specs[0].name == "Wireless"
