"""Turn an uploaded document of any supported type into clean Markdown text.

PDF goes through the layout-aware pymupdf4llm converter; Word documents are
walked in body order (paragraphs and tables interleaved) and rendered as
Markdown so headings and spec tables survive; Markdown and plain text are read
as-is. Every format finishes with the same `clean_text()` pass so the
extraction subagent never sees format-specific noise.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePath

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from qsg_synthesizer.tools.pdf_parser import (
    PdfRejectedError,
    ScannedPdfError,
    extract_text_from_pdf,
)
from qsg_synthesizer.tools.text_cleaning import clean_text

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})
_TEXT_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})

# A .docx is a zip; refuse archives that could expand into something huge.
_MAX_DOCX_MEMBERS = 1000
_MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

_HEADING_STYLE_RE = re.compile(r"^heading (\d)$", re.IGNORECASE)

_KIND_BY_EXTENSION = {".pdf": "PDF", ".docx": "Word document"}


class UnsupportedDocumentError(ValueError):
    """The file's extension is not one of SUPPORTED_EXTENSIONS."""


class DocumentParseError(ValueError):
    """The file's contents could not be read as the type its extension claims."""


class DocumentRejectedError(DocumentParseError):
    """The file can be read but won't be processed. The message is shown to
    the user after the file name, e.g. "has 900 pages; the limit is 200."."""


def extension_of(filename: str) -> str:
    return PurePath(filename or "").suffix.lower()


def is_supported(filename: str) -> bool:
    return extension_of(filename) in SUPPORTED_EXTENSIONS


def file_kind(filename: str) -> str:
    """Human name for error messages: 'PDF', 'Word document' or 'text file'."""
    return _KIND_BY_EXTENSION.get(extension_of(filename), "text file")


def extract_text(filename: str, contents: bytes) -> str:
    """Extract clean Markdown text from `contents`, dispatching on the extension."""
    ext = extension_of(filename)
    try:
        if ext == ".pdf":
            return extract_text_from_pdf(contents)
        if ext == ".docx":
            return clean_text(_docx_to_markdown(contents))
        if ext in _TEXT_EXTENSIONS:
            return clean_text(_decode_text(contents))
    except (DocumentParseError, ScannedPdfError):
        raise
    except PdfRejectedError as exc:
        raise DocumentRejectedError(str(exc)) from exc
    except Exception as exc:
        raise DocumentParseError(f"Could not read {filename!r}") from exc
    raise UnsupportedDocumentError(f"Unsupported file type: {ext or filename!r}")


def _decode_text(contents: bytes) -> str:
    text = contents.decode("utf-8-sig", errors="replace")
    if "\x00" in text:
        raise DocumentParseError("Binary content in a text file")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _check_docx_archive(contents: bytes) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile as exc:
        raise DocumentParseError("Not a valid .docx file") from exc
    with archive:
        members = archive.infolist()
        if len(members) > _MAX_DOCX_MEMBERS:
            raise DocumentParseError("Word document has too many parts")
        if sum(m.file_size for m in members) > _MAX_DOCX_UNCOMPRESSED_BYTES:
            raise DocumentParseError("Word document expands to too much data")
        if "word/document.xml" not in {m.filename for m in members}:
            raise DocumentParseError("Not a Word document")


def _docx_to_markdown(contents: bytes) -> str:
    _check_docx_archive(contents)
    document = Document(io.BytesIO(contents))
    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            block = _paragraph_to_markdown(Paragraph(child, document))  # type: ignore[arg-type]
        elif child.tag.endswith("}tbl"):
            block = _table_to_markdown(Table(child, document))  # type: ignore[arg-type]
        else:
            continue
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _paragraph_to_markdown(paragraph: Paragraph) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    if style.lower() == "title":
        return f"# {text}"
    heading = _HEADING_STYLE_RE.match(style)
    if heading:
        return f"{'#' * min(int(heading.group(1)), 6)} {text}"
    if style.lower().startswith("list bullet"):
        return f"- {text}"
    if style.lower().startswith("list number"):
        return f"1. {text}"
    return text


def _cell_text(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _table_to_markdown(table: Table) -> str:
    rows = [[_cell_text(cell.text) for cell in row.cells] for row in table.rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "|" + "---|" * width,
        *("| " + " | ".join(row) + " |" for row in rows[1:]),
    ]
    return "\n".join(lines)
