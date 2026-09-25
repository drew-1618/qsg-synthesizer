"""Deterministic PDF text extraction.

Turns a PDF's raw bytes into clean, layout-preserved Markdown so downstream
extraction subagents never have to look at layout noise (page numbers,
legal footers) that has nothing to do with device specs. Uses pymupdf4llm's
layout-aware conversion rather than a flat per-page text dump, since
real-world QSGs commonly use multi-column layouts and bordered tables that a
naive text extractor would garble.
"""

from __future__ import annotations

import re

import pymupdf
import pymupdf4llm

# Substring (not whole-line) removal: pymupdf4llm's layout reconstruction
# often merges nearby text into a single flowing paragraph line, so a
# boilerplate footer can end up sharing a line with real content. Each
# pattern is scoped to stop at the boilerplate clause's own end (a period,
# or the specific closing phrase) rather than dropping the whole line.
_BOILERPLATE_PATTERNS = [
    re.compile(r"fcc compliance\b[^\n]*?\.", re.IGNORECASE),
    re.compile(r"(?:©|\(c\))\s*\d{4}[^\n]*?all rights reserved\.?", re.IGNORECASE),
    re.compile(r"\bpage\s+\d+\s+of\s+\d+\.?", re.IGNORECASE),
]

_EXCESSIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")
_EXCESSIVE_SPACES_RE = re.compile(r"[ \t]{2,}")


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract cleaned Markdown from a PDF's raw bytes.

    Converts the document to layout-preserved Markdown (tables come out as
    proper Markdown tables), strips common boilerplate (FCC compliance
    notices, copyright footers, "Page X of Y" markers) wherever it appears -
    even sharing a line with real content - and collapses whitespace left
    behind by that removal.
    """
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = pymupdf4llm.to_markdown(document)
    finally:
        document.close()

    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)

    lines = [_EXCESSIVE_SPACES_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
