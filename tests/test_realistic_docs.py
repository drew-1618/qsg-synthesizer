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
