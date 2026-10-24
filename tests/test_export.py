"""Unit tests for Markdown/PDF battlecard rendering (escaping and empty lists)."""

from datetime import datetime

from app.schemas import BattlecardResponse
from app.services.export import (
    battlecard_to_markdown,
    battlecard_to_pdf_bytes,
    default_export_basename,
    sanitize_export_filename,
)


def _card(name_a="A", name_b="B", **overrides) -> BattlecardResponse:
    fields = dict(
        comparison_matrix={
            "model_name": {"device_a": name_a, "device_b": name_b},
            "default_ip": {"device_a": None, "device_b": "192.168.1.1"},
            "required_tools": {"device_a": [], "device_b": ["a | b"]},
        },
        installation_friction_score_device_a=2,
        installation_friction_score_device_b=7,
        key_risks_device_a=[],
        key_risks_device_b=[],
        key_advantages_device_a=[],
        key_advantages_device_b=[],
        sales_talk_track="Talk.",
    )
    fields.update(overrides)
    return BattlecardResponse(**fields)


class TestMarkdown:
    def test_pipes_in_cells_and_names_are_escaped(self):
        md = battlecard_to_markdown(_card(name_a="Foo | Bar"))
        assert "| Attribute | Foo \\| Bar | B |" in md
        assert "a \\| b" in md

    def test_newlines_in_cells_do_not_break_rows(self):
        card = _card()
        card.comparison_matrix["required_tools"]["device_b"] = ["x\ny"]
        md = battlecard_to_markdown(card)
        assert "| Required Tools | None | x y |" in md

    def test_empty_lists_and_missing_values(self):
        md = battlecard_to_markdown(_card())
        assert "| Default IP | Unknown | 192.168.1.1 |" in md
        assert md.count("- None identified.") == 4

    def test_missing_matrix_rows_render_as_unknown(self):
        md = battlecard_to_markdown(_card())
        assert "| Wi-Fi Standard | Unknown | Unknown |" in md


class TestPdf:
    def test_markup_characters_do_not_break_rendering(self):
        card = _card(
            name_a="<b>Acme & Co",
            sales_talk_track="<script> & </para>",
            key_risks_device_a=["needs <tool> & more"],
        )
        card.comparison_matrix["required_tools"]["device_b"] = ["<x>", "a & b"]
        assert battlecard_to_pdf_bytes(card).startswith(b"%PDF")

    def test_empty_lists_render(self):
        assert battlecard_to_pdf_bytes(_card()).startswith(b"%PDF")


class TestDefaultFilename:
    WHEN = datetime(2026, 10, 24, 16, 5)

    def test_slugs_both_device_names_and_date(self):
        card = _card(name_a="NovaFiber GX400", name_b="LegacyLink RG-210")
        assert (
            default_export_basename(card, self.WHEN)
            == "battlecard-novafiber-gx400-vs-legacylink-rg-210-2026-10-24"
        )

    def test_unicode_is_transliterated_and_symbols_collapse(self):
        card = _card(name_a="Café  Router (v2)!", name_b="Ünï / Box")
        assert (
            default_export_basename(card, self.WHEN)
            == "battlecard-cafe-router-v2-vs-uni-box-2026-10-24"
        )

    def test_empty_or_unsluggable_names_fall_back(self):
        card = _card(name_a="!!!", name_b="日本語")
        assert (
            default_export_basename(card, self.WHEN)
            == "battlecard-device-a-vs-device-b-2026-10-24"
        )

    def test_long_names_are_capped(self):
        card = _card(name_a="a" * 200, name_b="b" * 200)
        name = default_export_basename(card, self.WHEN)
        assert name == f"battlecard-{'a' * 40}-vs-{'b' * 40}-2026-10-24"


class TestSanitizeFilename:
    DEFAULT = "battlecard-x-vs-y-2026-10-24"

    def clean(self, requested, ext="pdf"):
        return sanitize_export_filename(requested, ext, self.DEFAULT)

    def test_adds_extension_and_replaces_spaces(self):
        assert self.clean("Q4 competitive card") == "Q4-competitive-card.pdf"

    def test_missing_or_blank_uses_default(self):
        assert self.clean(None) == f"{self.DEFAULT}.pdf"
        assert self.clean("   ") == f"{self.DEFAULT}.pdf"
        assert self.clean("...") == f"{self.DEFAULT}.pdf"

    def test_existing_export_extension_is_not_doubled(self):
        assert self.clean("card.PDF") == "card.pdf"
        assert self.clean("card.md.md", "md") == "card.md"

    def test_path_components_are_dropped(self):
        assert self.clean("../../etc/passwd") == "passwd.pdf"
        assert self.clean("C:\\temp\\card.pdf") == "card.pdf"
        assert self.clean("reports/") == "reports.pdf"

    def test_header_breaking_characters_are_removed(self):
        result = self.clean('a"b\r\nSet-Cookie: x=1;')
        assert result.endswith(".pdf")
        assert not set(result) & set('"\r\n;:/\\ ')

    def test_unicode_is_transliterated(self):
        assert self.clean("Café – Ünïcode") == "Cafe-Unicode.pdf"

    def test_length_is_capped(self):
        result = self.clean("x" * 500)
        assert result == "x" * 100 + ".pdf"


class TestDatasheetExports:
    def _unscored(self, **overrides):
        card = _card(
            installation_friction_score_device_a=None,
            installation_friction_score_device_b=None,
            warnings=["A: Only the first 8 of 9 sections were read."],
            **overrides,
        )
        card.comparison_matrix["extra_specs"] = [
            {"name": "Power | draw", "device_a": "10 W", "device_b": None}
        ]
        return card

    def test_markdown_handles_missing_scores_spec_rows_and_notes(self):
        md = battlecard_to_markdown(self._unscored())
        assert "Not compared: install effort is only scored" in md
        assert "None / 10" not in md
        assert "| Power \\| draw | 10 W | Not listed |" in md
        assert "## Notes" in md
        assert "- A: Only the first 8 of 9 sections were read." in md

    def test_scored_cards_have_no_notes_section(self):
        assert "## Notes" not in battlecard_to_markdown(_card())

    def test_pdf_renders_unscored_cards(self):
        assert battlecard_to_pdf_bytes(self._unscored()).startswith(b"%PDF")


def test_undocumented_provisioning_is_not_shown_as_the_console_fallback():
    card = _card()
    card.comparison_matrix["provisioning_method"] = {
        "device_a": "Console",
        "device_b": "Console",
    }
    card.comparison_matrix["provisioning_methods"] = {
        "device_a": [],
        "device_b": ["Console"],
    }
    md = battlecard_to_markdown(card)
    assert "| Provisioning Method | Not specified | Console |" in md
