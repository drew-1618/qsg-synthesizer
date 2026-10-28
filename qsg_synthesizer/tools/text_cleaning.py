"""Boilerplate and whitespace cleanup shared by every document parser.

Documents arrive as PDF, Word, Markdown or plain text, but the extraction
subagent should see the same clean text from all of them: no compliance
footers, page markers or runs of blank lines that have nothing to do with
device specs.
"""

from __future__ import annotations

import re

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
_EXCESSIVE_SPACES_RE = re.compile(r" {2,}")
# A tab or a wide run of spaces inside a line separates the columns of a
# plain-text table; on consecutive lines that have one, it's kept as one tab
# so the parser can still split the columns.
_COLUMN_GAP_RE = re.compile(r" *\t[ \t]*| {3,}")


def clean_text(text: str) -> str:
    """Strip boilerplate wherever it appears and collapse the whitespace it
    leaves. Column gaps are found first, so the gap a removed footer leaves
    isn't mistaken for one."""
    lines = _mark_column_gaps(text.splitlines())
    text = "\n".join(lines)
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)

    lines = [
        "\t".join(
            _EXCESSIVE_SPACES_RE.sub(" ", cell).strip() for cell in line.split("\t")
        )
        for line in text.splitlines()
    ]
    text = "\n".join(line.rstrip("\t") for line in lines)
    text = _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def _mark_column_gaps(lines: list[str]) -> list[str]:
    """Turn column gaps into single tabs in tables and into spaces elsewhere
    (stray spacing in prose). A table is a run of lines that each have a gap
    between words or an indent, at least two of them between words; an
    indented row has an empty first cell (it continues the row above, as
    merged cells do), while indented lines on their own are just indented."""
    internal = [bool(_COLUMN_GAP_RE.search(line.strip())) for line in lines]
    gapped = [
        bool(line.strip()) and (inside or bool(_COLUMN_GAP_RE.match(line)))
        for line, inside in zip(lines, internal)
    ]
    in_table = [False] * len(lines)
    start = 0
    while start < len(lines):
        end = start
        while end < len(lines) and gapped[end]:
            end += 1
        if sum(internal[start:end]) >= 2:
            in_table[start:end] = [True] * (end - start)
        start = end + 1
    marked = []
    for line, table_row in zip(lines, in_table):
        lead = "\t" if table_row and _COLUMN_GAP_RE.match(line) else ""
        marked.append(
            lead + _COLUMN_GAP_RE.sub("\t" if table_row else " ", line.strip())
        )
    return marked
