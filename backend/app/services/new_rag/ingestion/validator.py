"""
Shared anchor-matching logic, used by both structure_parser.py (Stage 2
validation) and chunker.py (parent chunk slicing) - kept in one place
so both stay consistent rather than each reimplementing matching rules.

Root cause this exists to handle, confirmed live against real books during
implementation - two distinct whitespace defects in pypdf's text extraction:
1. Against uploads/science.pdf: PDF line-wrapping preserved as a literal
   newline in the middle of a heading (e.g. "What are the essential
   components of\nfood?"). An LLM naturally reports a heading as one
   unbroken line.
2. Against a Class 10 Science NCERT book: some PDFs also insert a spurious
   SPACE in the middle of a single word (e.g. "CHEMICAL" extracts as
   "CHEMIC AL") - confirmed this is a text-run-splitting artifact in the
   source PDF itself (same category of defect that broke a literal
   "Answers" -> "Answ er s" extraction elsewhere), not something collapsing
   repeated text (see pdf_parser.py::collapse_repeated_runs) fixes on
   its own.

Both are solved uniformly by stripping ALL whitespace from both the anchor
and the page text before comparing, rather than only tolerating whitespace
BETWEEN words (which handles defect 1 but not defect 2). The tradeoff -
losing word-boundary awareness - is a non-issue in practice: anchors are
long, specific phrases (numbers, capitalized headings), so an accidental
match after removing all whitespace is not realistically possible. What is
NOT tolerated is any actual character difference - a genuinely wrong or
hallucinated anchor still fails to match, which is the whole point of
verifying it at all.
"""
import re
from typing import Optional


def find_anchor_position(page_text: str, anchor: str) -> Optional[int]:
    """
    Returns the character index in `page_text` where `anchor` starts,
    tolerant of any whitespace difference (missing/extra/misplaced spaces
    and newlines, anywhere) but not of any actual content difference.
    Returns None if no match is found - callers must treat that as a real
    validation failure, never fall back to guessing a position.
    """
    if not anchor or not anchor.strip():
        return None
    anchor_stripped = re.sub(r"\s+", "", anchor)
    if not anchor_stripped:
        return None

    # Build the whitespace-free text alongside an index map back to the
    # original string, so the match position can be translated back to a
    # real offset in page_text (needed by chunker.py for slicing).
    stripped_chars = []
    index_map = []
    for i, ch in enumerate(page_text):
        if not ch.isspace():
            stripped_chars.append(ch)
            index_map.append(i)
    stripped_text = "".join(stripped_chars)

    pos = stripped_text.find(anchor_stripped)
    if pos == -1:
        return None
    return index_map[pos]


def anchor_found(page_text: str, anchor: str) -> bool:
    return find_anchor_position(page_text, anchor) is not None
