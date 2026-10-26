"""The built-in parser on documents it wasn't written against.

`tests/fixtures/realistic/` holds vendor-style quick start guides and
datasheets (fictional devices) laid out the way real ones are: other heading
names, combined hardware tables, one column per model, PDF-style bold
headings, plain pasted text. The sample docs are pinned here too, so a
parser change that shifts what they extract shows up as a deliberate edit.
"""

from pathlib import Path

import pytest

from qsg_synthesizer.schemas import DeviceSpec, ProvisioningMethod
from qsg_synthesizer.services import orchestrator
from qsg_synthesizer.services.orchestrator import (
    THIN_INSTALL_WARNING,
    THIN_SPECS_WARNING,
    _heuristic_extract,
    _heuristic_extract_qsg,
    _normalize_to_device_spec,
    extract_device_spec,
    synthesize_battlecard,
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
        ["QR/Mobile App", "TR-069", "Web GUI"],
        0,
        4,
        5,
        "quick_start_guide",
        0,
    ),
    "device_a_qsg.pdf": (
        "NovaFiber GX400",
        "192.168.10.1",
        ["QR/Mobile App", "TR-069", "Web GUI"],
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
        assert not [note for note in notes if "models" in note]

    def test_value_and_notes_columns_are_not_models(self):
        text = (
            "# Acme Datasheet\n\n## Specifications\n\n| Spec | Value | Notes |\n"
            "|---|---|---|\n| Power | 10 W | typical |\n"
        )
        extraction, notes = _heuristic_extract(text, "x")
        assert extraction.specs[0].value == "10 W"
        assert not [note for note in notes if "models" in note]

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


class TestKeywordsStepsAndNames:
    @pytest.mark.parametrize(
        "name, device",
        [
            ("router_qsg.md", "AX3000 Dual-Band Wi-Fi 6 Router Arcwave RT-AX58"),
            ("modem_qsg.txt", "Zentra ZX-110 DOCSIS 3.1 Cable Modem"),
            ("ont_qsg_pdf.md", "Brightline ONT-2410"),
            ("ap_qsg.md", "Skyreach AP-630"),
            ("switch_datasheet.md", "EdgeLink 5400 Series"),
        ],
    )
    def test_device_name_skips_generic_title_words(self, name, device):
        assert _fixture(name).device_name == device

    @pytest.mark.parametrize(
        "name, methods",
        [
            ("router_qsg.md", ["Web GUI", "QR/Mobile App"]),
            ("modem_qsg.txt", ["Web GUI"]),
            ("ont_qsg_pdf.md", ["TR-069", "Web GUI"]),
            ("ap_qsg.md", ["DHCP", "Web GUI", "Console"]),
            ("switch_datasheet.md", ["Web GUI", "DHCP"]),
        ],
    )
    def test_provisioning_wordings(self, name, methods):
        assert [m.value for m in _fixture(name).provisioning_methods] == methods

    def test_methods_in_one_sentence_keep_their_order(self):
        text = "# Z1\n\nManage it with the web UI, a serial console or DHCP.\n"
        assert [
            m.value for m in _heuristic_extract_qsg(text, "z").provisioning_methods
        ] == [
            "Web GUI",
            "Console",
            "DHCP",
        ]

    def test_a_sentence_starting_with_no_is_negated(self):
        text = "# Z1\n\nNo screwdriver, console cable, or laptop is required.\n"
        assert _heuristic_extract_qsg(text, "z").provisioning_methods == []

    def test_wrapped_lines_are_one_sentence(self):
        text = (
            "# Z1\n\n- **DHCP**: LAN-side clients receive addresses from the\n"
            "  gateway's built-in DHCP server.\n"
        )
        assert _heuristic_extract_qsg(text, "z").provisioning_methods == []

    def test_a_support_link_is_not_a_web_gui(self):
        text = "# Z1\n\nSee https://support.example.com for help.\n"
        assert _heuristic_extract_qsg(text, "z").provisioning_methods == []

    @pytest.mark.parametrize(
        "name, steps",
        [
            ("router_qsg.md", 6),
            ("modem_qsg.txt", 5),
            ("ont_qsg_pdf.md", 5),
            ("ap_qsg.md", 9),
            ("switch_datasheet.md", None),
        ],
    )
    def test_numbered_setup_steps_are_counted(self, name, steps):
        assert _fixture(name).step_count == steps

    @pytest.mark.parametrize(
        "name, steps",
        [
            ("device_a_qsg.md", 4),
            ("device_a_qsg.pdf", 4),
            ("device_b_qsg.md", 7),
            ("device_b_qsg.pdf", 7),
        ],
    )
    def test_sample_steps_match_between_text_and_pdf(self, name, steps):
        assert _heuristic_extract_qsg(_sample_text(name), name).step_count == steps

    def test_tools_named_in_prose(self):
        assert _fixture("router_qsg.md").required_tools == ["Computer or smartphone"]
        text = "# Z1\n\nYou'll need a laptop, a ladder and a drill for mounting.\n"
        assert _heuristic_extract_qsg(text, "z").required_tools == [
            "Laptop",
            "Ladder",
            "Drill",
        ]

    def test_prose_saying_tools_are_not_needed_adds_none(self):
        text = "# Z1\n\nYou will not need a screwdriver.\nYou need to log in.\n"
        assert _heuristic_extract_qsg(text, "z").required_tools == []


def _spec(name: str) -> DeviceSpec:
    return extract_device_spec((FIXTURES_DIR / name).read_text(), name)


class TestConfidence:
    @pytest.mark.parametrize(
        "name, document_type",
        [
            ("router_qsg.md", "quick_start_guide"),
            ("modem_qsg.txt", "quick_start_guide"),
            ("ont_qsg_pdf.md", "quick_start_guide"),
            ("ap_qsg.md", "quick_start_guide"),
            ("switch_datasheet.md", "datasheet"),
            ("gateway_datasheet.md", "datasheet"),
        ],
    )
    def test_document_types(self, name, document_type):
        assert _fixture(name).document_type.value == document_type

    def test_label_value_rows_make_a_datasheet_without_a_specs_heading(self):
        text = (
            "# Acme Z1 Router\n\n## Hardware\n\n- Ports: 4 x 1G\n- Power: 12 W\n"
            "- Weight: 1 kg\n- Dimensions: 20 x 10 cm\n- Wi-Fi: 802.11ax\n"
        )
        extraction = _heuristic_extract_qsg(text, "z")
        assert extraction.document_type.value == "datasheet"
        assert extraction.wifi_standard == "802.11ax"
        assert {s.name for s in extraction.specs} >= {"Ports", "Weight"}

    def test_numbered_setup_steps_keep_a_spec_heavy_guide_a_guide(self):
        text = (
            "# Acme Z1\n\n## Specifications\n\n- Power: 12 W\n\n"
            "## Installation\n\n1. Mount it.\n2. Power it on.\n"
        )
        assert _heuristic_extract_qsg(text, "z").document_type.value == (
            "quick_start_guide"
        )

    def test_a_mounting_section_alone_does_not_make_a_datasheet_a_guide(self):
        text = (
            "# Acme Z1\n\n## Mounting\n\nWall or ceiling.\n\n"
            "## Specifications\n\n- Power: 12 W\n"
        )
        assert _heuristic_extract_qsg(text, "z").document_type.value == "datasheet"

    def test_rich_documents_get_no_thin_read_warning(self):
        for name in FIXTURES:
            assert THIN_INSTALL_WARNING not in _spec(name).extraction_warnings
            assert THIN_SPECS_WARNING not in _spec(name).extraction_warnings
        for name in ("device_a_qsg.md", "device_b_qsg.md", "device_c_datasheet.md"):
            assert (
                extract_device_spec(_sample_text(name), name).extraction_warnings == []
            )

    def test_a_guide_with_little_install_detail_is_flagged(self):
        spec = extract_device_spec("# Acme Z1\n\nPlug it in and enjoy.\n", "z")
        assert spec.extraction_warnings == [THIN_INSTALL_WARNING]

    def test_a_datasheet_with_few_specs_is_flagged(self):
        spec = extract_device_spec("# Acme Z1 Datasheet\n\n- Power: 12 W\n", "z")
        assert spec.extraction_warnings == [THIN_SPECS_WARNING]

    def test_guide_against_datasheet_is_not_scored(self):
        card = synthesize_battlecard(
            _spec("router_qsg.md"), _spec("switch_datasheet.md")
        )
        assert card.installation_friction_score_device_a is None
        assert card.unscored_reason is None  # the document types explain it

    def test_two_documented_guides_are_scored(self):
        card = synthesize_battlecard(_spec("ont_qsg_pdf.md"), _spec("router_qsg.md"))
        assert card.installation_friction_score_device_a is not None
        assert card.unscored_reason is None

    def test_an_undocumented_guide_is_not_scored_as_console_setup(self):
        known = DeviceSpec(
            model_name="A",
            provisioning_method=ProvisioningMethod.TR069,
            provisioning_methods=["TR-069"],
        )
        unknown = DeviceSpec(
            model_name="B", provisioning_method=ProvisioningMethod.CONSOLE
        )
        card = synthesize_battlecard(known, unknown, "sales")
        assert card.installation_friction_score_device_a is None
        assert card.installation_friction_score_device_b is None
        assert card.unscored_reason == (
            "Install effort isn't compared because the document for B doesn't say "
            "how the device is set up or provisioned."
        )
        assert card.sales_talk_track.startswith(card.unscored_reason)
        assert not any("console" in risk for risk in card.key_risks_device_b)

    def test_both_guides_undocumented_names_both(self):
        a = DeviceSpec(model_name="A", provisioning_method=ProvisioningMethod.CONSOLE)
        b = a.model_copy(update={"model_name": "B"})
        assert synthesize_battlecard(a, b).unscored_reason == (
            "Install effort isn't compared because the documents for A and B don't "
            "say how the devices are set up or provisioned."
        )
