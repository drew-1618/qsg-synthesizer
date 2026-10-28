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


# A PDF with less text than this across its pages, but with images, is a scan
# (or photos of pages): there's nothing for the text pipeline to read.
_SCANNED_MAX_TEXT_CHARS = 40
# Scanned pages sent to an AI model for transcription, at most, and the size
# they're rendered at: about what vision models downscale to anyway.
MAX_SCANNED_PAGES = 20
_PAGE_IMAGE_LONG_EDGE_PX = 1568
_PAGE_IMAGE_JPEG_QUALITY = 80


class PdfRejectedError(ValueError):
    """A readable PDF the app won't process; the message is user-facing and
    completes a sentence starting with the file name."""


class ScannedPdfError(ValueError):
    """A PDF whose pages are images with no text layer; its pages can be
    rendered with `render_pdf_pages` for an AI model to read."""

    def __init__(self, page_count: int):
        super().__init__(f"Scanned PDF with {page_count} page(s) and no text layer")
        self.page_count = page_count


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
        if _is_scanned(document):
            raise ScannedPdfError(document.page_count)
        text = pymupdf4llm.to_markdown(document)
    finally:
        document.close()

    return clean_text(text)


def _is_scanned(document: pymupdf.Document) -> bool:
    text_chars = 0
    has_images = False
    for page in document:
        text_chars += len("".join(page.get_text().split()))
        if text_chars >= _SCANNED_MAX_TEXT_CHARS:
            return False
        has_images = has_images or bool(page.get_images())
    return has_images


def render_pdf_pages(
    pdf_bytes: bytes, max_pages: int = MAX_SCANNED_PAGES
) -> list[bytes]:
    """The first `max_pages` pages as JPEG images, scaled so the longer edge
    is about what vision models read at (larger costs more and reads no
    better)."""
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        images = []
        for page in document.pages(0, min(max_pages, document.page_count)):
            scale = _PAGE_IMAGE_LONG_EDGE_PX / max(page.rect.width, page.rect.height)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            images.append(pixmap.tobytes("jpg", jpg_quality=_PAGE_IMAGE_JPEG_QUALITY))
        return images
    finally:
        document.close()
