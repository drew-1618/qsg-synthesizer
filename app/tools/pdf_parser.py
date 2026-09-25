"""Deterministic PDF text extraction.

Turns a PDF's raw bytes into clean, boilerplate-free text so downstream
extraction subagents never have to look at layout noise (page numbers,
legal footers) that has nothing to do with device specs.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

_BOILERPLATE_PATTERNS = [
    re.compile(r"fcc compliance", re.IGNORECASE),
    re.compile(r"all rights reserved", re.IGNORECASE),
    re.compile(r"^page\s+\d+\s+of\s+\d+$", re.IGNORECASE),
]

_EXCESSIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract cleaned text from a PDF's raw bytes.

    Reads pages sequentially, drops lines matching common boilerplate
    (FCC compliance notices, copyright footers, "Page X of Y" markers), and
    collapses runs of blank lines down to a single blank line.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    pages_text = [page.extract_text() or "" for page in reader.pages]
    raw_text = "\n".join(pages_text)

    kept_lines = []
    for raw_line in raw_text.splitlines():
        stripped = raw_line.strip()
        if stripped and any(
            pattern.search(stripped) for pattern in _BOILERPLATE_PATTERNS
        ):
            continue
        kept_lines.append(stripped)

    text = "\n".join(kept_lines)
    text = _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
