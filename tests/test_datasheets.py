"""Datasheet support: document types, open-ended specs, optional friction,
and section-by-section extraction of long documents."""

from pathlib import Path

import pytest

from app.models.schemas import DocumentType, QSGExtraction, SpecItem
from app.schemas import DeviceSpec, ProvisioningMethod, SpecValue
from app.services import orchestrator
from app.services.orchestrator import (
    NO_RISKS_MESSAGE,
    _build_key_risks,
    _heuristic_extract_qsg,
    _merge_extractions,
    _split_into_chunks,
    _validate_extraction,
    extract_device_spec,
    synthesize_battlecard,
)

SAMPLE_DOCS_DIR = Path(__file__).parent.parent / "sample_docs"


def _sample(name: str) -> str:
    return (SAMPLE_DOCS_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Force the heuristic parser regardless of any keys in the environment."""
    monkeypatch.setattr(orchestrator, "_select_llm_provider", lambda: None)


class TestDocumentType:
    def test_datasheet_title(self):
        extraction = _heuristic_extract_qsg(_sample("device_c_datasheet.md"), "c.md")
        assert extraction.document_type == DocumentType.DATASHEET
        assert extraction.device_name == "Corvane AX9000"

    def test_quick_start_guides_stay_quick_start_guides(self):
        for name in ("device_a_qsg.md", "device_b_qsg.md"):
            extraction = _heuristic_extract_qsg(_sample(name), name)
            assert extraction.document_type == DocumentType.QUICK_START_GUIDE

    def test_specifications_without_install_content_is_a_datasheet(self):
        text = "# Acme Z1\n\n## Specifications\n\n- Power: 10 W\n"
        assert _heuristic_extract_qsg(text, "z.md").document_type == (
            DocumentType.DATASHEET
        )

    def test_ambiguous_documents_default_to_quick_start_guide(self):
        text = "# Acme Z1\n\nProvision over the web GUI.\n"
        assert _heuristic_extract_qsg(text, "z.md").document_type == (
            DocumentType.QUICK_START_GUIDE
        )


class TestSpecs:
    def test_bullets_become_open_ended_specs_excluding_fixed_fields(self):
        spec = extract_device_spec(_sample("device_c_datasheet.md"), "c.md")
        names = [s.name for s in spec.specs]
        assert "Power Consumption" in names and "Certifications" in names
        assert "Wi-Fi Standard" not in names and "WAN Speed" not in names
        assert spec.wifi_standard == "802.11be (Wi-Fi 7), tri-band"

    def test_spec_tables_fill_fixed_fields_and_skip_the_header_row(self):
        spec = extract_device_spec(_sample("device_d_datasheet.md"), "d.md")
        assert spec.wifi_standard == "802.11ax (Wi-Fi 6), dual-band"
        assert spec.wan_speed == "Up to 2.5 Gbps (GPON)"
        names = [s.name for s in spec.specs]
        assert "Specification" not in names
        assert names[0] == "Max Wireless Throughput"

    def test_duplicate_and_empty_specs_are_dropped_and_rows_are_capped(
        self, monkeypatch
    ):
        monkeypatch.setattr(orchestrator, "_MAX_SPEC_ROWS", 3)
        specs = orchestrator._normalize_specs(
            [
                SpecItem(name="Power", value="10 W"),
                SpecItem(name="power:", value="12 W"),
                SpecItem(name="Weight", value=" "),
                SpecItem(name="Form factor", value="Desktop"),
                SpecItem(name="A", value="1"),
                SpecItem(name="B", value="2"),
                SpecItem(name="C", value="3"),
            ]
        )
        assert [(s.name, s.value) for s in specs] == [
            ("Power", "10 W"),
            ("A", "1"),
            ("B", "2"),
        ]

    def test_extra_spec_rows_merge_case_insensitively_in_a_then_b_order(self):
        a = DeviceSpec(
            model_name="A",
            provisioning_method=ProvisioningMethod.WEB_GUI,
            specs=[
                SpecValue(name="Power", value="10 W"),
                SpecValue(name="Weight", value="1 kg"),
            ],
        )
        b = DeviceSpec(
            model_name="B",
            provisioning_method=ProvisioningMethod.WEB_GUI,
            specs=[
                SpecValue(name="Battery", value="Yes"),
                SpecValue(name="POWER", value="8 W"),
            ],
        )
        rows = synthesize_battlecard(a, b).comparison_matrix["extra_specs"]
        assert rows == [
            {"name": "Power", "device_a": "10 W", "device_b": "8 W"},
            {"name": "Weight", "device_a": "1 kg", "device_b": None},
            {"name": "Battery", "device_a": None, "device_b": "Yes"},
        ]

    def test_malformed_optional_fields_from_an_llm_are_dropped(self):
        payload = {
            "device_name": "X",
            "source_document": "x.md",
            "document_type": "brochure",
            "specs": "lots",
            "wifi_standard": "Wi-Fi 6",
        }
        extraction = _validate_extraction(payload)
        assert extraction.document_type is None
        assert extraction.specs == []
        assert extraction.wifi_standard == "Wi-Fi 6"


class TestFrictionApplicability:
    def test_two_datasheets_are_not_scored(self):
        c = extract_device_spec(_sample("device_c_datasheet.md"), "c.md")
        d = extract_device_spec(_sample("device_d_datasheet.md"), "d.md")
        card = synthesize_battlecard(c, d, "neutral")
        assert card.installation_friction_score_device_a is None
        assert card.installation_friction_score_device_b is None
        assert card.sales_talk_track.startswith("Install effort isn't compared")
        assert "Corvane AX9000 stands out for" in card.sales_talk_track

    def test_a_guide_against_a_datasheet_is_not_scored(self):
        a = extract_device_spec(_sample("device_a_qsg.md"), "a.md")
        d = extract_device_spec(_sample("device_d_datasheet.md"), "d.md")
        card = synthesize_battlecard(a, d, "sales")
        assert card.installation_friction_score_device_a is None
        assert "Lead with what NovaFiber GX400 offers" in card.sales_talk_track

    def test_two_guides_are_still_scored(self):
        a = extract_device_spec(_sample("device_a_qsg.md"), "a.md")
        b = extract_device_spec(_sample("device_b_qsg.md"), "b.md")
        card = synthesize_battlecard(a, b)
        assert isinstance(card.installation_friction_score_device_a, int)
        assert isinstance(card.installation_friction_score_device_b, int)

    def test_unknown_document_type_is_treated_as_a_guide(self):
        spec = DeviceSpec(
            model_name="X", provisioning_method=ProvisioningMethod.WEB_GUI
        )
        card = synthesize_battlecard(spec, spec.model_copy(update={"model_name": "Y"}))
        assert card.installation_friction_score_device_a is not None

    def test_sales_spec_track_without_advantages_says_so(self):
        spec = DeviceSpec(
            model_name="X",
            provisioning_method=ProvisioningMethod.WEB_GUI,
            document_type="datasheet",
        )
        other = spec.model_copy(update={"model_name": "Y"})
        track = synthesize_battlecard(spec, other, "sales").sales_talk_track
        assert "No spec advantages for X were identified" in track

    def test_datasheets_without_provisioning_get_no_console_risks(self):
        spec = DeviceSpec(
            model_name="X",
            provisioning_method=ProvisioningMethod.CONSOLE,
            default_ip="192.168.1.1",
            is_private_ip=True,
            document_type="datasheet",
        )
        assert _build_key_risks(spec) == [NO_RISKS_MESSAGE]
        # A guide with the same (fallback) Console method keeps the risks.
        guide = spec.model_copy(update={"document_type": "quick_start_guide"})
        assert len(_build_key_risks(guide)) == 2

    def test_extraction_warnings_reach_the_battlecard(self):
        a = DeviceSpec(
            model_name="A",
            provisioning_method=ProvisioningMethod.WEB_GUI,
            extraction_warnings=["Only the first 8 of 9 sections were read."],
        )
        b = a.model_copy(update={"model_name": "B", "extraction_warnings": []})
        assert synthesize_battlecard(a, b).warnings == [
            "A: Only the first 8 of 9 sections were read."
        ]


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert _split_into_chunks("# A\n\nbody\n", max_chars=100) == ["# A\n\nbody\n"]

    def test_sections_are_packed_without_splitting_headings(self):
        text = "".join(f"# Section {i}\n" + "x" * 40 + "\n" for i in range(6))
        chunks = _split_into_chunks(text, max_chars=120)
        assert "".join(chunks) == text
        assert all(len(c) <= 120 for c in chunks)
        assert all(c.startswith("# Section") for c in chunks)
        assert len(chunks) == 3

    def test_oversized_section_is_cut_at_blank_lines_then_hard(self):
        text = "# Big\n" + ("para\n\n" * 30) + ("y" * 250)
        chunks = _split_into_chunks(text, max_chars=100)
        assert "".join(chunks) == text
        assert all(len(c) <= 100 for c in chunks)

    def test_merge_prefers_first_values_and_unions_lists(self):
        def part(**fields):
            base = {"device_name": "Box", "source_document": "label"}
            return QSGExtraction.model_validate({**base, **fields})

        merged = _merge_extractions(
            [
                part(wifi_standard=None, capabilities=["Mesh"], step_count=3),
                part(
                    device_name="Other",
                    wifi_standard="Wi-Fi 6",
                    wan_speed="1G",
                    capabilities=["mesh", "VoIP"],
                    step_count=7,
                    specs=[{"name": "Power", "value": "10 W"}],
                ),
                part(
                    wifi_standard="Wi-Fi 7", specs=[{"name": "power", "value": "12 W"}]
                ),
            ],
            "big.pdf",
        )
        assert merged.device_name == "Box"
        assert merged.source_document == "big.pdf"
        assert merged.wifi_standard == "Wi-Fi 6"
        assert merged.capabilities == ["Mesh", "VoIP"]
        assert merged.step_count == 7
        assert [(s.name, s.value) for s in merged.specs] == [("Power", "10 W")]


class TestLongDocuments:
    def _long_text(self, sections=3):
        size = orchestrator.MAX_LLM_INPUT_CHARS // 2
        return "".join(f"# Part {i}\n" + "z" * size + "\n" for i in range(sections))

    def _use_fake_llm(self, monkeypatch, fail_on=()):
        calls = []

        def fake(text, label):
            calls.append(label)
            if any(marker in label for marker in fail_on):
                raise RuntimeError("boom")
            return QSGExtraction(
                device_name="Big Box" if "part 1 of" in label else "unnamed",
                source_document=label,
                capabilities=["Shared", f"Cap from {label.split(';')[0]}"],
            )

        monkeypatch.setattr(orchestrator, "_select_llm_provider", lambda: "anthropic")
        monkeypatch.setattr(orchestrator, "_provider_ready", lambda provider: True)
        monkeypatch.setitem(orchestrator._PROVIDER_EXTRACTORS, "anthropic", fake)
        return calls

    def test_long_documents_are_read_in_parts_and_merged(self, monkeypatch):
        calls = self._use_fake_llm(monkeypatch)
        spec = extract_device_spec(self._long_text(), "big.md")
        assert len(calls) == 3
        assert calls[0] == "big.md (part 1 of 3)"
        assert "big.md (part 2 of 3; device: Big Box)" in calls
        assert spec.model_name == "Big Box"
        assert spec.extraction_source == "llm"
        assert spec.capabilities.count("Shared") == 1
        assert len(spec.capabilities) == 4
        assert spec.extraction_warnings == []

    def test_too_many_parts_reads_the_first_ones_and_warns(self, monkeypatch):
        monkeypatch.setattr(orchestrator, "_MAX_CHUNKS", 2)
        calls = self._use_fake_llm(monkeypatch)
        spec = extract_device_spec(self._long_text(sections=4), "big.md")
        assert len(calls) == 2
        assert spec.extraction_warnings == [
            "Only the first 2 of 4 sections were read; the document is too long "
            "to read in full."
        ]

    def test_a_failed_later_part_is_skipped_with_a_warning(self, monkeypatch):
        self._use_fake_llm(monkeypatch, fail_on=("part 2 of",))
        spec = extract_device_spec(self._long_text(), "big.md")
        assert spec.extraction_source == "llm"
        assert spec.extraction_warnings == [
            "1 of 3 sections could not be read and were skipped."
        ]

    def test_a_failed_first_part_falls_back_to_the_heuristic(self, monkeypatch):
        self._use_fake_llm(monkeypatch, fail_on=("part 1 of",))
        spec = extract_device_spec(self._long_text(), "big.md")
        assert spec.extraction_source == "heuristic"

    def test_short_documents_still_use_a_single_call(self, monkeypatch):
        calls = self._use_fake_llm(monkeypatch)
        extract_device_spec("# Small\n\nShort guide.\n", "small.md")
        assert calls == ["small.md"]
