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
_EXCESSIVE_SPACES_RE = re.compile(r"[ \t]{2,}")


def clean_text(text: str) -> str:
    """Strip boilerplate wherever it appears and collapse the whitespace it leaves."""
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)

    lines = [_EXCESSIVE_SPACES_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
