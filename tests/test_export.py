"""Unit tests for Markdown/PDF battlecard rendering (escaping and empty lists)."""

from app.schemas import BattlecardResponse
from app.services.export import battlecard_to_markdown, battlecard_to_pdf_bytes


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
