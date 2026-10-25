"""Deterministic PDF text extraction.

Turns a PDF's raw bytes into clean, layout-preserved Markdown so downstream
extraction subagents never have to look at layout noise (page numbers,
legal footers) that has nothing to do with device specs. Uses pymupdf4llm's
layout-aware conversion rather than a flat per-page text dump, since
real-world QSGs commonly use multi-column layouts and bordered tables that a
naive text extractor would garble.
"""

from __future__ import annotations

import pymupdf
import pymupdf4llm

from qsg_synthesizer.tools.text_cleaning import clean_text

# Quick start guides and datasheets run to a few dozen pages; layout analysis
# of a huge PDF would tie up a worker thread for minutes.
MAX_PDF_PAGES = 200


class PdfRejectedError(ValueError):
    """A readable PDF the app won't process; the message is user-facing and
    completes a sentence starting with the file name."""


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
        if document.needs_pass:
            raise PdfRejectedError(
                "is password-protected. Save a copy without the password and "
                "upload that instead."
            )
        if document.page_count > MAX_PDF_PAGES:
            raise PdfRejectedError(
                f"has {document.page_count} pages; the limit is {MAX_PDF_PAGES}. "
                "Upload just the setup or specifications section."
            )
        text = pymupdf4llm.to_markdown(document)
    finally:
        document.close()

    return clean_text(text)
