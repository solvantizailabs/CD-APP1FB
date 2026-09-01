"""
Raw content extraction directly from the source PDF: page text, per-page
printed page numbers, embedded diagram images (+ captions), and table-like
text blocks. Everything here pulls content out of the PDF itself - page/topic
STRUCTURE detection (what those pages/headings mean) lives in
structure_parser.py instead. See docs/RAG_REDESIGN_PLAN.md, sections 3 and 5.

Page-number detection: replaces the old approach of detecting a page number
once per chapter (on page 2) and extrapolating every subsequent page by
simple arithmetic offset - that approach is the confirmed root cause of the
page-number bug described in the plan doc: any irregular page early in a
chapter (an image page, a stray front-matter page) silently breaks every
page number after it, because nothing re-checks the actual printed number.
Here, the same footer-regex technique already proven reliable elsewhere in
this codebase (see books.py::pre_analyze_books) is applied to every page
individually.

Diagrams: embedded images are extracted directly from the PDF (pypdf exposes
these as real PIL images per page - confirmed working against
uploads/science.pdf during implementation), saved to disk by the caller, and
captioned via a vision-capable LLM call. The caption is what gets embedded
later (locked design: never embed raw pixel content, embed a
natural-language description that points back to the image).

A PDF page's embedded images include page-furniture (a repeated watermark,
recurring margin icons) alongside real content - pypdf extracts everything
indiscriminately, since it's just reading the file format's image objects,
not recognizing what they show. flag_boilerplate_images() below is a
deterministic (hash-based, no LLM) post-extraction filter that identifies
which extracted images are actually the same recurring page-furniture
asset repeated chapter-wide, so the caller can exclude them before they
ever reach the caption LLM - see its docstring for why this matters (a
boilerplate image given per-page topic context gets hallucinated into a
fake, topic-specific caption instead of being recognized as decorative).

Tables: true structural table extraction (rows/columns) is NOT reliable from
plain pypdf text alone - pypdf's extract_text() does not preserve column
layout. What's implemented here is a best-effort heuristic that flags
candidate table-like text blocks (multiple consecutive short lines with
several whitespace-separated numeric/short tokens) so they can at least be
tagged and reviewed separately from ordinary prose, rather than silently
chunked as regular paragraphs. This is an honest first pass, not a claim of
robust table structure extraction - a dedicated layout-aware parser would be
a real future improvement, noted as a limitation rather than solved here.
"""
import base64
import hashlib
import io
import json
import os
import re
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from pypdf import PdfReader

from backend.app.services.new_rag.retry import call_with_retry
from backend.app.services.new_rag import rate_governor
from backend.app.services.new_rag.prompts import load_prompt

logger = logging.getLogger(__name__)

# pypdf's default zlib decompression guard (75MB) exists as a zip-bomb safety
# limit, but real NCERT chapter PDFs with large embedded images/diagrams
# (e.g. jesc108.pdf, ~18MB on disk) can legitimately exceed it and raise
# LimitReachedError on a normal extract_text() call. The existing production
# ingestion (books.py::process_batch_ingest_in_background) already raises
# this same limit for the same reason - matching that fix here rather than
# reinventing a different one, since it's a known-good, already-proven
# workaround for real files in this exact codebase.
try:
    import pypdf.filters as _pypdf_filters
    _pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH = 500_000_000
except Exception:
    pass

# --- Page text extraction ---

# NCERT prints a running footer on every page after the chapter opener,
# with the real page number always appearing FIRST (leftmost) in the page's
# first non-empty text line, e.g. "1VII Science Free Distribution...",
# "Food Components2", "Science8 88 88". That last example is real, confirmed
# live against a Class 10 Science PDF during implementation: some pages in
# some NCERT files have a text-rendering artifact (likely a bold/shadow
# heading style) that duplicates small text elements - the real page number
# "8" is followed by corrupted duplicate garbage "88 88". The old
# leading/trailing-preference heuristic grabbed the trailing garbage on
# these pages; confirmed the fix is simpler than that heuristic, not more
# complex: the real number is reliably the FIRST digit-group in the line
# (after stripping a "20XX-XX" year-range pattern, which is the other
# confirmed false-positive source - see below), full stop, no leading/
# trailing preference logic needed.
_YEAR_RANGE_RE = re.compile(r"\b20\d{2}-\d{2}\b")
_DIGIT_GROUP_RE = re.compile(r"\d{1,4}")


def _digit_candidates(line: str) -> List[int]:
    cleaned, _ = _YEAR_RANGE_RE.subn("", line)
    return [int(m) for m in _DIGIT_GROUP_RE.findall(cleaned)]


def detect_textbook_page_number(page_text: str) -> Optional[int]:
    """
    Deterministic, regex-only detection of a single page's own printed page
    number from its footer. Returns None if no digits are found at all -
    callers must not guess in that case, only interpolate from confirmed
    neighbors (see structure_parser.py::resolve_page_sequence). When digits
    ARE found, returns the leftmost group - see module comment for why
    that's the reliable choice, confirmed against real duplication artifacts.
    """
    if not page_text:
        return None
    first_line = next((l.strip() for l in page_text.split("\n") if l.strip()), "")
    if not first_line:
        return None
    candidates = _digit_candidates(first_line)
    return candidates[0] if candidates else None


# One optional whitespace character tolerated at each repeat boundary too -
# confirmed live some corrupted runs alternate between a trailing space and
# no trailing space on successive repeats (e.g. "5.4 TR 5.4 TR5.4 TR..."),
# which a strict exact-backreference match misses on alternating instances.
# Max unit length raised from 40 to 60 after a real miss was found live: a
# genuine 42-character repeated heading ("2.3 HOW STRONG ARE ACID OR BASE
# SOLUTIONS?") was completely un-collapsed because it exceeded the old cap
# by 2 characters - the whole match silently failed rather than partially
# matching, so this went undetected until spotted in a real retrieved chunk.
_REPEAT_RUN_RE = re.compile(r"(.{3,60}?)(?:\s?\1)+")


def collapse_repeated_runs(text: str) -> str:
    """
    Collapses a short text fragment that is immediately repeated one or
    more times back down to a single copy. Confirmed live against a real
    Class 10 Science NCERT PDF that some headings/footers extract with a
    genuine source-level duplication artifact (likely a bold/shadow text
    rendering technique this specific PDF uses) - e.g. the real text
    "3.2 CHEMIC3.2 CHEMIC3.2 CHEMIC3.2 CHEMIC3.2 CHEMIC AL PROPERTIES..."
    collapses correctly to "3.2 CHEMIC AL PROPERTIES...". Tested against
    real clean text with legitimately repeated words (e.g. "Example 2.1
    Example 2.2", "the the cat sat on the the mat" - the latter a genuine
    doubled-word typo, correctly collapsed) with no false positives found -
    the pattern only matches IMMEDIATELY adjacent repeats, so words that
    recur elsewhere on the page are never touched. Runs to a fixed point
    (repeated application) since collapsing one run can expose another.
    """
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        text = _REPEAT_RUN_RE.sub(r"\1", text)
    return text


# Adobe Symbol-font Private Use Area mapping (2026-08-21, found via real
# pilot ingestion: worked-example anchors in math-heavy chapters - circle
# mensuration, trigonometry, statistics with formulas - consistently failed
# Stage 2's verbatim anchor verification, even after a retry). Root cause,
# confirmed by comparing raw extracted bytes against the LLM's transcription
# of the same page: these NCERT PDFs embed math/Greek glyphs (π, °, Δ, ×,
# ∠, etc.) using the legacy Adobe "Symbol" font encoding. pypdf's text
# extraction, lacking a usable ToUnicode CMap for this font, falls back to
# the PDF-extraction convention of remapping an unmapped glyph to the
# Private Use Area at 0xF000 + the glyph's original Symbol-encoding code
# point (confirmed live: raw text contained U+F070 exactly where "π" should
# be - 0x70 is 'p', the Symbol-encoding slot for pi; U+F044 where "Δ" should
# be - 0x44 is 'D', the Symbol-encoding slot for Delta). The LLM, reading the
# page visually/from training familiarity, naturally transcribes the correct
# real character, so anchor text can never match character-for-character
# without this translation - not a retry-able failure, a deterministic one.
#
# First pass (this same day) only covered ~20 hand-picked entries and missed
# a real production case: Statistics/Surface-Areas-and-Volumes use a much
# wider slice of this font (capital Greek for summation notation, fraction/
# bracket layout pieces, set/logic operators) - confirmed live via 30 more
# distinct leftover codepoints found in real re-ingested chunks after the
# first fix. This is now the FULL standard Adobe Symbol Encoding (PDF
# Reference Appendix D / the PostScript Symbol font's documented encoding
# vector - a fixed, decades-old standard, not a guess), covering every
# printable slot 0x20-0x7E and the extended math/Greek/symbol block
# 0xA0-0xF7. A handful of codes (0xE6-0xEF, 0xF8-0xFE) are deliberately
# EXCLUDED, not guessed at: they are multi-piece bracket/brace-stretching
# glyphs (the separate top/middle/bottom segments a PDF renderer uses to
# draw one large curly brace or tall bracket around a stacked fraction) -
# there is no single correct Unicode character for "the middle third of a
# large bracket", so these are mapped to "" (removed) rather than
# substituted with something wrong. Even after this, a genuinely
# 2-dimensional layout (a fraction's numerator stacked over its
# denominator) will still read somewhat run-together in plain extracted
# text - that's a separate, deeper limitation of linear text extraction
# from visually-laid-out math, not something a character-mapping table can
# fix on its own.
_SYMBOL_FONT_PUA_MAP = {
    # --- Basic Latin range (0x20-0x7E): identity fallback for positions
    # that are visually identical to plain ASCII (digits, most punctuation,
    # space) - confirmed live this renderer routes those through the SAME
    # 0xF000+code PUA convention too, not only the genuinely-different
    # Symbol-encoding positions (Greek letters, math operators). Built
    # programmatically below, then overridden by the explicit table for
    # positions that actually differ from ASCII.
    **{0xF000 + code: chr(code) for code in range(0x20, 0x7F)},
    # --- Positions that differ from plain ASCII: Greek alphabet (occupies
    # the Latin-letter slots in Symbol encoding) and a handful of math/logic
    # operators in the punctuation range.
    0xF022: "∀", 0xF024: "∃", 0xF027: "∋", 0xF02A: "∗", 0xF02D: "−",
    0xF041: "Α", 0xF042: "Β", 0xF043: "Χ", 0xF044: "Δ", 0xF045: "Ε",
    0xF046: "Φ", 0xF047: "Γ", 0xF048: "Η", 0xF049: "Ι", 0xF04A: "ϑ",
    0xF04B: "Κ", 0xF04C: "Λ", 0xF04D: "Μ", 0xF04E: "Ν", 0xF04F: "Ο",
    0xF050: "Π", 0xF051: "Θ", 0xF052: "Ρ", 0xF053: "Σ", 0xF054: "Τ",
    0xF055: "Υ", 0xF056: "ς", 0xF057: "Ω", 0xF058: "Ξ", 0xF059: "Ψ",
    0xF05A: "Ζ", 0xF05C: "∴", 0xF05E: "⊥",
    0xF061: "α", 0xF062: "β", 0xF063: "χ", 0xF064: "δ", 0xF065: "ε",
    0xF066: "φ", 0xF067: "γ", 0xF068: "η", 0xF069: "ι", 0xF06A: "ϕ",
    0xF06B: "κ", 0xF06C: "λ", 0xF06D: "μ", 0xF06E: "ν", 0xF06F: "ο",
    0xF070: "π", 0xF071: "θ", 0xF072: "ρ", 0xF073: "σ", 0xF074: "τ",
    0xF075: "υ", 0xF076: "ϖ", 0xF077: "ω", 0xF078: "ξ", 0xF079: "ψ",
    0xF07A: "ζ", 0xF07E: "∼",
    # --- Extended math/symbol block (0xA0-0xF7) ---
    0xF0A1: "ϒ", 0xF0A2: "′", 0xF0A3: "≤", 0xF0A4: "⁄", 0xF0A5: "∞",
    0xF0A6: "ƒ", 0xF0A7: "♣", 0xF0A8: "♦", 0xF0A9: "♥", 0xF0AA: "♠",
    0xF0AB: "↔", 0xF0AC: "←", 0xF0AD: "↑", 0xF0AE: "→", 0xF0AF: "↓",
    0xF0B0: "°", 0xF0B1: "±", 0xF0B2: "″", 0xF0B3: "≥", 0xF0B4: "×",
    0xF0B5: "∝", 0xF0B6: "∂", 0xF0B7: "•", 0xF0B8: "÷", 0xF0B9: "≠",
    0xF0BA: "≡", 0xF0BB: "≈", 0xF0BC: "…", 0xF0BF: "↵",
    0xF0C0: "ℵ", 0xF0C1: "ℑ", 0xF0C2: "ℜ", 0xF0C3: "℘", 0xF0C4: "⊗",
    0xF0C5: "⊕", 0xF0C6: "∅", 0xF0C7: "∩", 0xF0C8: "∪", 0xF0C9: "⊃",
    0xF0CA: "⊇", 0xF0CB: "⊄", 0xF0CC: "⊂", 0xF0CD: "⊆", 0xF0CE: "∈",
    0xF0CF: "∉",
    0xF0D0: "∠", 0xF0D1: "∇", 0xF0D2: "®", 0xF0D3: "©", 0xF0D4: "™",
    0xF0D5: "∏", 0xF0D6: "√", 0xF0D7: "⋅", 0xF0D8: "¬", 0xF0D9: "∧",
    0xF0DA: "∨", 0xF0DB: "⇔", 0xF0DC: "⇐", 0xF0DD: "⇑", 0xF0DE: "⇒",
    0xF0DF: "⇓",
    0xF0E5: "∑", 0xF0F2: "∫",
    # Deliberately NOT mapped: 0xE6-0xEF and 0xF8-0xFE (bracket/brace/
    # integral-sign STRETCH PIECES - top/middle/bottom segments of a large
    # drawn bracket, not standalone characters). Handled below by removal.
}
_SYMBOL_FONT_STRETCH_PIECES = set(range(0xF0E6, 0xF0F0)) | set(range(0xF0F3, 0xF0FF))
_SYMBOL_FONT_ALL_CODES = sorted(set(_SYMBOL_FONT_PUA_MAP) | _SYMBOL_FONT_STRETCH_PIECES)
_SYMBOL_FONT_RE = re.compile("[" + "".join(chr(c) for c in _SYMBOL_FONT_ALL_CODES) + "]")


def normalize_symbol_font_chars(text: str) -> str:
    """Translates known Adobe Symbol-font PUA artifacts back to real Unicode
    math/Greek characters, and removes the handful of bracket/integral
    stretch-piece codes that have no single correct Unicode equivalent. See
    module comment above for the confirmed root cause. Safe no-op on text
    that doesn't contain any of these codepoints - only ever substitutes
    exact, individually-confirmed mappings from the documented standard
    Symbol encoding, never a blanket PUA-range guess."""
    if not text:
        return text
    return _SYMBOL_FONT_RE.sub(lambda m: _SYMBOL_FONT_PUA_MAP.get(ord(m.group(0)), ""), text)


# Decorative drop-cap / stylized-heading duplication artifact (2026-08-22,
# found via real ingestion: a Class 10 Social Science chapter's "Rainwater
# Harvesting" section heading extracted as "R RRRRAINWATER  H H H ARVESTING"
# - confirmed by reading the raw extracted text directly). Some PDFs render
# a section heading's leading letter as an oversized/stylized "drop cap"
# using a layered or emboss/shadow font effect - pypdf's text extraction
# then emits that one glyph several times in a row (with inconsistent
# spacing between repeats) before the rest of the word continues normally.
#
# CONFIRMED DANGEROUS if matched too loosely: an earlier version of this
# regex (same-letter, 3+ times, no other constraint) also matched genuine
# Roman numerals - "World War III would be catastrophic." was silently
# corrupted to "World War I would be catastrophic." on real Social Science
# content, a real factual change, not a formatting nicety, caught before
# this ever ran against real ingested data. Fixed with a lookahead requiring
# the repeated run to be glued DIRECTLY (no space) into 2+ more letters with
# no word boundary in between - the actual signature of the drop-cap
# artifact (repeated letter immediately continuing into the rest of the same
# word, e.g. "RRRR" + "AINWATER"). Refined further after testing: also
# needed to catch the space-separated variant ("H H H ARVESTING"), but a
# plain "letters follow" lookahead re-introduces the Roman-numeral risk
# ("World War III would..." - "would" is also just letters). The signal
# that actually distinguishes them: this artifact only ever happens inside
# an all-caps decorative heading (confirmed: "RAINWATER HARVESTING" is
# itself printed fully uppercase in the source), while a real Roman numeral
# sits inside normal sentence-case prose - the word immediately after it
# ("would", "is", "began"...) is always lowercase, never another run of
# capital letters. Requiring the continuation to be UPPERCASE is what
# safely tells "H H H ARVESTING" apart from "World War III would" - both
# are structurally "repeated letter, then space, then more letters," so
# this is the one piece of context that reliably distinguishes them.
# "World War II" is separately safe regardless, since it only has 2
# occurrences and this pattern requires 3+.
_LETTER_STUTTER_RE = re.compile(r"\b([A-Z])(?:\s?\1){2,}(?=\s?[A-Z]{2,})")


def collapse_letter_stutter(text: str) -> str:
    """Collapses a decorative drop-cap letter-duplication artifact (e.g.
    "R RRRRAINWATER" -> "RAINWATER") back to a single instance of the
    repeated letter. See module comment above for the confirmed root cause."""
    if not text:
        return text
    return _LETTER_STUTTER_RE.sub(lambda m: m.group(1), text)


# --- Column-aware page-text extraction ---
#
# pypdf's page.extract_text() has no concept of columns - it linearizes text
# in whatever order the PDF's content stream happens to emit drawing
# operations, which is NOT guaranteed to match visual reading order on a
# multi-column page. Confirmed live against jess101.pdf page 4 (Class 10
# Social, "Resources and Development"): the tail of the left column
# (a Gandhiji quote's closing half, continuing from page 3, plus the Club of
# Rome/Schumacher/Brundtland Commission paragraph and the "LAND RESOURCES"
# heading) was emitted AFTER the right column's "LAND UTILISATION" heading
# and its numbered list, instead of before it - splicing two unrelated
# topics' text together mid-chunk downstream and mislabeling the misplaced
# block under the wrong topic. This uses pdfplumber's word-level bounding
# boxes (x0/x1/top/bottom per word - not available from pypdf) to detect a
# real column gutter and reassemble text in true left-column-then-right-
# column reading order instead of trusting pypdf's internal ordering.
_GUTTER_MIN_WIDTH_FRACTION = 0.02   # min gap between the two margin clusters, relative to page width
_GUTTER_CENTER_MIN_FRACTION = 0.25  # the two margins' midpoint must sit within [0.25, 0.75] of page width
_GUTTER_CENTER_MAX_FRACTION = 0.75
_COLUMN_MIN_LINE_FRACTION = 0.15    # each side needs at least this fraction of the page's lines
_LINE_Y_TOLERANCE = 3.0             # px tolerance for grouping words onto the same line


def _group_words_by_row(words: List[Dict], y_tol: float = _LINE_Y_TOLERANCE) -> List[List[Dict]]:
    """Groups word dicts (each with 'text','x0','x1','top') into visual rows
    by y-position, top-to-bottom, each row's words ordered left-to-right.
    Returns the raw word-lists (not yet joined to text) - on a two-column
    page a "row" here can genuinely contain words from BOTH columns at once
    (whenever a left-column line and a right-column line happen to sit at
    the same height, which is common) - see _split_row_by_gutter, which is
    what actually separates those back out; this function only groups by
    height, nothing else."""
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: w["top"])
    rows = []
    current = [words_sorted[0]]
    current_top = words_sorted[0]["top"]
    for w in words_sorted[1:]:
        if abs(w["top"] - current_top) <= y_tol:
            current.append(w)
        else:
            rows.append(current)
            current = [w]
            current_top = w["top"]
    rows.append(current)
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    rows.sort(key=lambda r: r[0]["top"])
    return rows


def _row_to_line_dict(row_words: List[Dict]) -> Dict:
    return {
        "top": min(w["top"] for w in row_words),
        "x0": min(w["x0"] for w in row_words),
        "x1": max(w["x1"] for w in row_words),
        "text": " ".join(w["text"] for w in row_words),
    }


def _group_words_into_lines(words: List[Dict], y_tol: float = _LINE_Y_TOLERANCE) -> List[Dict]:
    """Groups words into line dicts (top/x0/x1/text), top-to-bottom, without
    any column awareness - see _group_words_by_row for the row grouping
    this builds on."""
    return [_row_to_line_dict(row) for row in _group_words_by_row(words, y_tol)]


# A row's internal word-to-word gap this wide is no longer normal
# inter-word spacing (confirmed live against jess101.pdf: normal spacing
# between words on the same line is a few px, well under 10) - it's the
# signature of a row that actually contains BOTH columns' text side by side
# at the same height, and should be split back into two separate lines
# rather than read as one run-on line mixing two unrelated columns' words.
_ROW_INTERNAL_GUTTER_MIN_GAP = 12.0


def _split_row_by_gutter(row_words: List[Dict], min_gap: float = _ROW_INTERNAL_GUTTER_MIN_GAP) -> Tuple[List[Dict], List[Dict]]:
    """
    Looks for the largest internal x-gap within one already-height-grouped
    row of words. If it's wide enough to be a real column gutter (not just
    normal word spacing), splits the row there and returns (left_part,
    right_part) - both non-empty. If no such gap exists, returns
    (row_words, []) unchanged - this row is genuinely single-column content.
    """
    if len(row_words) < 2:
        return row_words, []
    best_i, best_gap = None, 0.0
    for i in range(1, len(row_words)):
        gap = row_words[i]["x0"] - row_words[i - 1]["x1"]
        if gap > best_gap:
            best_i, best_gap = i, gap
    if best_i is not None and best_gap >= min_gap:
        return row_words[:best_i], row_words[best_i:]
    return row_words, []


def _detect_column_margins(lines: List[Dict], page_width: float) -> Optional[Tuple[float, float]]:
    """
    Detects two-column layout from LINE start positions (x0), not full word
    spans. A line's start (left edge) is a reliable "column left margin"
    signal - nearly every line in a column begins flush against that
    column's margin - whereas a line's END (x1, ragged-right prose) varies
    too much to use for detecting the gutter itself (confirmed live: an
    earlier version tried to find a page-wide gap in merged [x0,x1] word
    spans and failed on every real two-column page in this chapter, because
    a single word anywhere in the gutter's x-range - one indented line, one
    wide heading - collapses the whole-page merge into one span covering
    nearly the full page width).

    Sorts line x0 values and looks for the single largest gap that splits
    them into two groups, each with a healthy share of the page's lines,
    centered roughly in the middle of the page. Returns
    (left_margin_x0, right_margin_x0) - the rightmost x0 in the left
    cluster and the leftmost x0 in the right cluster - or None if no such
    split exists (single-column page, or no reliable pattern).

    Outlier x0 values that occur only once or twice on the page (a stray
    footnote, a decorative drop-cap glyph, one oddly-indented line) are
    excluded before the gap search - confirmed live these single-occurrence
    values otherwise sit BETWEEN the two real column-margin clusters and
    get mistaken for the split point themselves (e.g. jess101.pdf page 4's
    true clusters are ~17 lines near x0=80-90 and ~30 lines near x0=325-360,
    but two unrelated single-occurrence x0 values at ~175 and ~275 sat
    between them, and the naive largest-adjacent-gap search picked the gap
    between THOSE two noise points instead of the real 90-to-325 gutter,
    since gaps are only ever compared to their immediate sorted neighbor).
    Real column margins are used by many lines each (every paragraph line
    in that column starts there), so requiring a minimum occurrence count
    reliably separates genuine margins from one-off noise.
    """
    if not lines or page_width <= 0:
        return None
    x0_counts: Dict[float, int] = defaultdict(int)
    for l in lines:
        x0_counts[round(l["x0"] / 5) * 5] += 1
    min_occurrences = max(2, int(len(lines) * 0.03))
    frequent_x0s = sorted(x0 for x0, count in x0_counts.items() if count >= min_occurrences)
    n = len(frequent_x0s)
    if n < 4:
        return None

    total_lines = len(lines)
    best = None
    best_gap = 0.0
    for i in range(1, n):
        gap = frequent_x0s[i] - frequent_x0s[i - 1]
        left_count = sum(c for x0, c in x0_counts.items() if x0 <= frequent_x0s[i - 1])
        right_count = sum(c for x0, c in x0_counts.items() if x0 >= frequent_x0s[i])
        center = (frequent_x0s[i - 1] + frequent_x0s[i]) / 2
        if (gap >= page_width * _GUTTER_MIN_WIDTH_FRACTION
                and left_count >= total_lines * _COLUMN_MIN_LINE_FRACTION
                and right_count >= total_lines * _COLUMN_MIN_LINE_FRACTION
                and page_width * _GUTTER_CENTER_MIN_FRACTION <= center <= page_width * _GUTTER_CENTER_MAX_FRACTION
                and gap > best_gap):
            best = (frequent_x0s[i - 1], frequent_x0s[i])
            best_gap = gap
    return best


def extract_page_text_column_aware(pdfplumber_page) -> Optional[str]:
    """
    Reassembles one page's text in true reading order: the whole left
    column top-to-bottom, then the whole right column top-to-bottom. Falls
    back to a plain top-to-bottom line order (still bounding-box-derived,
    just not column-split) when no reliable two-column margins are found -
    most pages in this chapter are single-column and already extract
    correctly via pypdf, so this path only needs to activate for genuine
    two-column layouts.

    Rows are grouped by height first (_group_words_by_row), then EACH ROW
    is individually checked for an internal gutter gap
    (_split_row_by_gutter) before being classified left/right - not a
    single global x-threshold applied to every word. Two earlier approaches
    were tried and confirmed live to fail on jess101.pdf page 4:
    1. Classifying already-merged lines (grouped by height only, no column
       awareness) by their x0/x1 range spliced unrelated columns' text
       together mid-line whenever a left-column row and a right-column row
       shared a height (common) - both columns' words end up in the same
       "line" text with no separation at all.
    2. Classifying individual WORDS by a single global x-midpoint (halfway
       between the two detected column margins) chopped genuine left-column
       lines in half mid-sentence, because a left column's own line
       legitimately extends rightward well past that midpoint (a column is
       usually much wider than the gap between the two margins' START
       positions) - words from the SAME line's tail end up wrongly
       reassigned to the "right" bucket.
    Splitting per-row at that row's own internal gap (real inter-word
    spacing is a few px; a genuine same-row cross-column gap is confirmed
    live to be >= _ROW_INTERNAL_GUTTER_MIN_GAP) avoids both failure modes:
    a true single-column row has no internal gap that wide and is
    classified as one whole line; a genuinely merged two-column row gets
    split exactly at its own real gutter, with both halves kept intact.

    Returns None (never raises) if pdfplumber can't extract words at all,
    so the caller can fall back to pypdf without losing the page.
    """
    try:
        words = pdfplumber_page.extract_words(use_text_flow=False, keep_blank_chars=False)
    except Exception as e:
        logger.warning(f"[NEW_RAG][Stage1] pdfplumber word extraction failed: {e}")
        return None
    if not words:
        return ""

    page_width = pdfplumber_page.width
    rows = _group_words_by_row(words)
    natural_lines = [_row_to_line_dict(r) for r in rows]
    margins = _detect_column_margins(natural_lines, page_width)

    if margins is None:
        return "\n".join(l["text"] for l in natural_lines)

    left_margin, right_margin = margins
    midpoint = (left_margin + right_margin) / 2

    left_lines, right_lines = [], []
    for row in rows:
        left_part, right_part = _split_row_by_gutter(row)
        if right_part:
            # Genuinely two columns' text sharing this row's height - both
            # halves are real, keep both.
            left_lines.append(_row_to_line_dict(left_part))
            right_lines.append(_row_to_line_dict(right_part))
        else:
            # Single-column row - classify the whole row by which margin
            # its own start is nearer to (never split it).
            line = _row_to_line_dict(row)
            if line["x0"] < midpoint:
                left_lines.append(line)
            else:
                right_lines.append(line)

    total_words = len(words)
    left_word_count = sum(len(l["text"].split()) for l in left_lines)
    right_word_count = sum(len(l["text"].split()) for l in right_lines)
    if (left_word_count < total_words * _COLUMN_MIN_LINE_FRACTION
            or right_word_count < total_words * _COLUMN_MIN_LINE_FRACTION):
        # Not a real balanced two-column page after all - don't force a
        # column split, just use natural top-to-bottom order.
        return "\n".join(l["text"] for l in natural_lines)

    left_lines.sort(key=lambda l: l["top"])
    right_lines.sort(key=lambda l: l["top"])
    return "\n".join(l["text"] for l in left_lines + right_lines)


def extract_raw_pages(pdf_path: str) -> List[Dict]:
    """
    Extract raw text and a per-page detected textbook_page for every page in
    the PDF. `textbook_page` is None here where detection failed on that
    specific page - structure_parser.py::resolve_page_sequence fills those in
    by interpolation, it is not done inline here so the raw detection result
    stays inspectable. Text is cleaned with normalize_symbol_font_chars(),
    collapse_letter_stutter(), and collapse_repeated_runs() before anything
    else sees it, since all three artifacts otherwise corrupt page-number
    detection, topic-heading anchors, AND general chunk text quality all at
    once - cleaning once here benefits every downstream stage.

    Text extraction defaults to pypdf's plain extract_text() for every page,
    unchanged from before - that already produces correct reading order on
    the large majority of pages (confirmed live: only 1 of 12 pages in
    jess101.pdf needed correction). The column-aware pdfplumber path
    (extract_page_text_column_aware, see its docstring) is only ATTEMPTED on
    a page whose pypdf text already fails find_reading_order_warnings()
    (structure_parser.py) - i.e. only pages that already look broken - and
    its result is only KEPT if it (a) isn't drastically shorter than pypdf's
    own output and (b) actually clears the warning it was trying to fix.

    This gating is deliberate, not a shortcut: an earlier version of this
    function ran column-detection unconditionally on every page and it was
    confirmed live to be a net regression - the gutter-detection heuristic
    false-positived on several genuinely single-column pages (ones with an
    off-center figure/caption fooling it into seeing two columns), silently
    breaking pages that were already correct, including page-number
    detection on some of them. Gating on the (separately validated, zero
    false positives across this same chapter) reading-order sanity check
    first means the fragile column-splitting logic only ever runs on pages
    that are already known to be broken, and self-verifies its own result
    instead of being trusted blindly.
    """
    from backend.app.services.new_rag.ingestion.structure_parser import find_reading_order_warnings
    import pdfplumber

    reader = PdfReader(pdf_path)
    plumber_pages = None
    try:
        plumber_doc = pdfplumber.open(pdf_path)
        plumber_pages = plumber_doc.pages
    except Exception as e:
        logger.warning(f"[NEW_RAG][Stage1] pdfplumber could not open {pdf_path!r}, "
                        f"falling back to pypdf-only extraction for all pages: {e}")

    pages = []
    for idx, page in enumerate(reader.pages):
        pypdf_text = page.extract_text() or ""
        text = pypdf_text
        column_aware_used = False

        if (find_reading_order_warnings(pypdf_text)
                and plumber_pages is not None and idx < len(plumber_pages)):
            column_text = extract_page_text_column_aware(plumber_pages[idx])
            # Defensive floor: only trust the column-aware result if it's not
            # drastically shorter than pypdf's own extraction (a sign the
            # word-level extraction missed content pypdf found some other
            # way) - never let a reordering bug silently drop text. And only
            # actually use it if it resolves the warning that triggered this
            # attempt in the first place - if it doesn't, the column-split
            # guess was wrong for this page too, so keep pypdf's version
            # rather than swap in an equally-unverified alternative.
            if (column_text is not None
                    and len(column_text) >= 0.7 * len(pypdf_text)
                    and not find_reading_order_warnings(column_text)):
                text = column_text
                column_aware_used = True

        text = normalize_symbol_font_chars(text)
        text = collapse_letter_stutter(text)
        text = collapse_repeated_runs(text)
        pages.append({
            "pdf_page": idx + 1,
            "text": text,
            "detected_textbook_page": detect_textbook_page_number(text),
            "column_aware_extraction_used": column_aware_used,
        })

    if plumber_pages is not None:
        plumber_doc.close()

    return pages


# --- Diagram extraction + captioning ---

_DIAGRAM_CAPTION_PROMPT_TEMPLATE = load_prompt("diagram_caption.txt")


def _format_caption_prompt(chapter_name: Optional[str] = None, topic_name: Optional[str] = None) -> str:
    """
    Fills the {context_block} placeholder. Falls back to an empty block when
    chapter_name/topic_name aren't passed in - keeps the prompt file's
    placeholder from ever being sent to the API literally unresolved,
    regardless of caller.
    """
    lines = []
    if chapter_name:
        lines.append(f"Chapter: {chapter_name}")
    if topic_name:
        lines.append(f"Topic: {topic_name}")
    context_block = ("\n".join(lines) + "\n") if lines else ""
    return _DIAGRAM_CAPTION_PROMPT_TEMPLATE.replace("{context_block}", context_block)


def extract_diagram_images(pdf_path: str, start_pdf_page: int, end_pdf_page: int,
                            min_size_px: int = 20) -> List[Dict]:
    """
    Extracts embedded images from the given PDF page range - deterministic,
    no LLM call, no caption yet. Split out from captioning (caption_diagram_image
    below) specifically so the caller can resolve each image's enclosing
    topic BEFORE captioning it and pass that context into the prompt - doing
    captioning inside this same extraction loop (the original design) meant
    every caption was generated in isolation from pixels alone, since the
    caller only learns which topic a diagram belongs to after extraction
    finishes. Skips only genuinely degenerate fragments (bullet points, thin
    rule lines) via the min_size_px filter.

    Bug fix (2026-08-25, found via live testing against jemh101.pdf, Class
    10 Maths "Real Numbers"): min_size_px was 110, meant to skip decorative
    icons - but confirmed live this also silently dropped a real, legitimate
    embedded image (a historical portrait photo in a sidebar box, 82x115px)
    before it ever reached classification. 110 was too blunt an instrument
    for "is this real content" - we already have a far more reliable signal
    for that exact question (is_content, from caption_diagram_image's own
    vision-model judgment, added earlier this session), so this floor's job
    is now only to filter out fragments too small to be a real image at all
    (a bullet glyph, a hairline rule), not to make a content judgment by
    pixel dimensions. Lowered to 20 - low enough that a genuinely small
    photo like this one survives to be classified on its actual content,
    high enough to still skip true noise.
    """
    reader = PdfReader(pdf_path)
    diagrams = []

    for pdf_page_idx in range(start_pdf_page - 1, end_pdf_page):
        if pdf_page_idx < 0 or pdf_page_idx >= len(reader.pages):
            continue
        page = reader.pages[pdf_page_idx]
        try:
            images = page.images
        except Exception as e:
            logger.warning(f"[NEW_RAG][Stage5] Could not read images on pdf_page={pdf_page_idx + 1}: {e}")
            continue

        for img_idx_on_page, img in enumerate(images):
            pil_img = img.image
            if pil_img is None:
                continue
            if pil_img.size[0] < min_size_px or pil_img.size[1] < min_size_px:
                continue

            buf = io.BytesIO()
            fmt = pil_img.format or "PNG"
            # OpenAI's vision endpoint only accepts png/jpeg/gif/webp -
            # confirmed live against jesc104.pdf that a page can embed an
            # image in a format PIL saves without error (TIFF) but the API
            # rejects outright (400 invalid_image_format), silently losing
            # that diagram's caption. Force conversion whenever the native
            # format isn't one of the four accepted ones, not only when the
            # save itself fails - "PIL can write it" and "OpenAI can read
            # it" are different guarantees, and only the second one matters
            # here.
            if fmt.upper() not in ("PNG", "JPEG", "GIF", "WEBP"):
                try:
                    pil_img.convert("RGB").save(buf, format="JPEG")
                    fmt = "JPEG"
                except Exception as e:
                    logger.warning(f"[NEW_RAG][Stage5] Could not convert unsupported format "
                                    f"{pil_img.format!r} to JPEG on pdf_page={pdf_page_idx + 1}: {e}")
                    continue
            else:
                try:
                    pil_img.save(buf, format=fmt)
                except Exception:
                    try:
                        buf = io.BytesIO()
                        pil_img.convert("RGB").save(buf, format="JPEG")
                        fmt = "JPEG"
                    except Exception as e:
                        # Unlike everywhere else in this file, this fallback had
                        # no isolation - a single malformed embedded image threw
                        # uncaught here and killed the whole chapter (confirmed
                        # live: a chapter died mid-Stage5 with no chunks.json
                        # ever written). Skip just this one image instead.
                        logger.warning(f"[NEW_RAG][Stage5] Could not encode image on "
                                        f"pdf_page={pdf_page_idx + 1}: {e}")
                        continue
            image_bytes = buf.getvalue()

            # pypdf's img.name is NOT guaranteed unique per page - confirmed
            # live against jesc101.pdf that it returns the generic "Im0" for
            # nearly every image regardless of how many are on the same
            # page. Building the saved filename from img.name alone caused
            # every second/third image on a page to silently overwrite the
            # first on disk (the captions in diagrams.json were still each
            # generated from the correct, distinct image before the
            # overwrite happened - only the saved file was wrong). Prefixing
            # a per-page loop index guarantees a unique filename regardless
            # of what pypdf calls the image internally.
            #
            # The extension must come from `fmt` (the ACTUAL bytes we just
            # wrote to `image_bytes`), not from img.name's own extension -
            # confirmed live (jess102.pdf) that pypdf's img.name carries the
            # PDF's original encoding extension (e.g. "Im2.tif") even when
            # the block above converted the real bytes to JPEG, silently
            # saving JPEG-encoded content under a ".tif"/".jp2" filename.
            # Every standard image viewer (browsers, Supabase Storage's own
            # preview, a future <img> tag) trusts the extension over
            # content-sniffing for inline rendering, so a mismatched
            # extension makes an otherwise-perfectly-valid image
            # unviewable anywhere except a tool that opens it byte-first
            # (e.g. PIL) - this was the actual cause of "only the watermark
            # PNG shows up as an image" when browsing extracted output.
            base_name = os.path.splitext(img.name)[0]
            ext = "jpg" if fmt.upper() == "JPEG" else fmt.lower()
            diagrams.append({
                "pdf_page": pdf_page_idx + 1,
                "image_name": f"{img_idx_on_page}_{base_name}.{ext}",
                "image_bytes": image_bytes,
                "image_format": fmt,
                "image_hash": hashlib.sha256(image_bytes).hexdigest(),
                "size": pil_img.size,
            })

    return diagrams


# --- Vector-drawn diagram extraction (2026-08-25) ---
#
# extract_diagram_images() above only finds embedded RASTER image objects
# (pypdf's page.images, which walks /Image-subtype XObjects) - confirmed
# live against jemh101.pdf (Class 10 Maths, "Real Numbers") that this is
# structurally blind to a real, important diagram: a factor tree built
# entirely from vector-drawn rectangles and connecting lines (a PDF /Form
# XObject containing raw drawing operators), with no image bytes anywhere
# for pypdf to find. This is common for mathematical/geometric figures
# (factor trees, number lines, geometric constructions), which are
# typically cheaper to typeset as vector drawing instructions than as
# embedded pictures - unlike photos/maps/charts, which usually are raster
# and already handled above.
#
# Approach: pdfplumber exposes the actual vector drawing primitives on a
# page (page.rects, page.lines, page.curves - the same objects a PDF
# renderer executes to draw the factor tree's boxes and connectors).
# Cluster nearby primitives into spatial groups, and treat a cluster as a
# real diagram candidate only if it has enough distinct primitives to be a
# structured figure rather than simple page decoration - confirmed live
# this distinction matters: a plain bordered sidebar box (e.g. the
# Gauss-portrait "fun fact" box on jemh101.pdf page 3) is drawn with
# exactly ONE rectangle and nothing else nearby, while the factor tree on
# page 2 is 15 rects (~11 factor boxes + a couple of decorative rules) plus
# 9 connecting lines, clustered tightly together - an order of magnitude
# more primitives in one place. Requiring a minimum cluster size
# (MIN_VECTOR_DIAGRAM_PRIMITIVES) is a simple, confirmed-live-effective way
# to keep the real diagram while excluding single-rectangle decorative
# boxes, without needing to distinguish "diagram" from "box border" by
# shape semantics.
#
# Once a candidate region's bounding box is found, it's cropped and
# rendered to a real PNG image and returned in the EXACT SAME shape as
# extract_diagram_images()'s raster results (pdf_page/image_name/
# image_bytes/image_format/image_hash/size) - deliberately, so this new
# source flows through the entire existing pipeline unchanged: boilerplate
# flagging, is_content classification, captioning, Stage 6/7 embedding, all
# of it. Only the extraction step gains a new source; nothing downstream
# needs to know or care that a given "diagram" came from a vector region
# instead of an embedded image object.
MIN_VECTOR_DIAGRAM_PRIMITIVES = 5
MIN_VECTOR_DIAGRAM_AREA_PX = 8000  # rejects small clusters of decorative marks (corner ticks, rule-line ends)
_VECTOR_CLUSTER_GAP = 15.0  # px - primitives within this distance of each other's bounding box are treated as one figure


def _cluster_bboxes(boxes: List[tuple], gap: float = _VECTOR_CLUSTER_GAP) -> List[List[tuple]]:
    """
    Groups (x0, top, x1, bottom) bounding boxes into clusters: any two boxes
    whose (gap-expanded) extents overlap end up in the same cluster,
    transitively. Simple iterative merge - the number of vector primitives
    on one page is small (tens, not thousands), so this doesn't need to be
    more sophisticated than repeated pairwise merging to a fixed point.
    """
    clusters = [[b] for b in boxes]
    changed = True
    while changed:
        changed = False
        merged: List[List[tuple]] = []
        absorbed = [False] * len(clusters)
        for i, cluster in enumerate(clusters):
            if absorbed[i]:
                continue
            current = list(cluster)
            cx0 = min(b[0] for b in current) - gap
            cy0 = min(b[1] for b in current) - gap
            cx1 = max(b[2] for b in current) + gap
            cy1 = max(b[3] for b in current) + gap
            for j in range(i + 1, len(clusters)):
                if absorbed[j]:
                    continue
                other = clusters[j]
                if any(not (b[2] < cx0 or b[0] > cx1 or b[3] < cy0 or b[1] > cy1) for b in other):
                    current.extend(other)
                    absorbed[j] = True
                    changed = True
                    cx0 = min(cx0, min(b[0] for b in other) - gap)
                    cy0 = min(cy0, min(b[1] for b in other) - gap)
                    cx1 = max(cx1, max(b[2] for b in other) + gap)
                    cy1 = max(cy1, max(b[3] for b in other) + gap)
            merged.append(current)
        clusters = merged
    return clusters


def extract_vector_diagram_regions(pdf_path: str, start_pdf_page: int, end_pdf_page: int,
                                    min_primitives: int = MIN_VECTOR_DIAGRAM_PRIMITIVES,
                                    min_area_px: int = MIN_VECTOR_DIAGRAM_AREA_PX,
                                    resolution: int = 150) -> List[Dict]:
    """
    Finds vector-drawn diagrams (see module comment above) and returns them
    cropped/rendered as real images, in the same dict shape
    extract_diagram_images() uses. Fails open per-page (a rendering error on
    one page never blocks the rest of the chapter) and returns [] entirely
    if pdfplumber can't open the file - vector-diagram extraction is a
    supplementary source on top of the raster path above, never a
    requirement for ingestion to proceed.
    """
    import pdfplumber

    diagrams: List[Dict] = []
    try:
        plumber_doc = pdfplumber.open(pdf_path)
    except Exception as e:
        logger.warning(f"[NEW_RAG][Stage5] pdfplumber could not open {pdf_path!r} for vector-diagram "
                        f"extraction (non-fatal, raster extraction is unaffected): {e}")
        return diagrams

    with plumber_doc:
        for pdf_page_idx in range(start_pdf_page - 1, min(end_pdf_page, len(plumber_doc.pages))):
            page = plumber_doc.pages[pdf_page_idx]
            try:
                boxes = (
                    [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects]
                    + [(min(l["x0"], l["x1"]), min(l["top"], l["bottom"]),
                        max(l["x0"], l["x1"]), max(l["top"], l["bottom"])) for l in page.lines]
                    + [(c["x0"], c["top"], c["x1"], c["bottom"]) for c in page.curves]
                )
            except Exception as e:
                logger.warning(f"[NEW_RAG][Stage5] Could not read vector primitives on "
                                f"pdf_page={pdf_page_idx + 1}: {e}")
                continue
            if len(boxes) < min_primitives:
                continue

            for idx, cluster in enumerate(_cluster_bboxes(boxes)):
                if len(cluster) < min_primitives:
                    continue
                x0 = max(0.0, min(b[0] for b in cluster) - 5)
                top = max(0.0, min(b[1] for b in cluster) - 5)
                x1 = min(page.width, max(b[2] for b in cluster) + 5)
                bottom = min(page.height, max(b[3] for b in cluster) + 5)
                if (x1 - x0) * (bottom - top) < min_area_px:
                    continue

                try:
                    cropped_image = page.within_bbox((x0, top, x1, bottom)).to_image(resolution=resolution)
                    pil_img = cropped_image.original
                except Exception as e:
                    logger.warning(f"[NEW_RAG][Stage5] Could not render vector-diagram region on "
                                    f"pdf_page={pdf_page_idx + 1}: {e}")
                    continue

                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                image_bytes = buf.getvalue()
                diagrams.append({
                    "pdf_page": pdf_page_idx + 1,
                    "image_name": f"vec{idx}_region.png",
                    "image_bytes": image_bytes,
                    "image_format": "PNG",
                    "image_hash": hashlib.sha256(image_bytes).hexdigest(),
                    "size": pil_img.size,
                })

    return diagrams


# Both conditions required so a real illustration that's deliberately reused
# twice in one chapter isn't wrongly excluded - real reuse is rare and
# low-count, watermark-style repetition is on nearly every page.
#
# BOILERPLATE_PAGE_FRACTION lowered 0.4 -> 0.25 (2026-08-25, confirmed live
# against jess101.pdf): a real decorative page-corner graphic repeated
# byte-identically on 4 of this chapter's 12 pages (33%) was getting missed
# by the old 0.4 cutoff and indexed as 4 separate "diagram" chunks with a
# hallucinated, topic-specific caption on each ("Geographical Resource...
# curved path... red dot" - a caption the model invented for a decoration
# with nothing to actually describe). MIN_OCCURRENCES=3 already does most of
# the real work here - genuine content is confirmed to essentially never
# repeat 3+ times verbatim in one chapter (each real diagram illustrates one
# specific concept once) - so 0.25 (i.e. "on at least 3 of a 12-page
# chapter's pages", scaling with chapter length) still requires real
# recurrence, just without demanding it appear on a near-majority of pages.
BOILERPLATE_MIN_OCCURRENCES = 3
BOILERPLATE_PAGE_FRACTION = 0.25


def flag_boilerplate_images(diagrams: List[Dict], total_pages: int) -> List[Dict]:
    """
    Flags recurring, byte-identical images (the NCERT "not to be
    republished" watermark, repeated margin icons) that appear on most/all
    pages of the chapter - real chapter diagrams are page-specific and never
    repeat this way. Confirmed live (jess102.pdf): the watermark and a
    generic margin icon are both embedded as distinct image objects on
    every single page, byte-for-byte identical - and because the caption
    LLM is given per-page chapter/topic context, it hallucinated a
    different, plausible-sounding but entirely fabricated caption for the
    same boilerplate image on every page instead of recognizing it as
    decorative (e.g. "Illustrates various types of flora and fauna..." on
    one page, "Shows the Project Tiger initiative..." on another, for
    pixel-identical bytes). Those fabricated captions were then embedded
    and retrievable as if they were real diagram content - a genuine
    grounding risk, not a cosmetic issue.

    Mutates each dict in place, adding `is_boilerplate` (image_hash is
    already present from extract_diagram_images). Caller is expected to
    skip captioning/saving/chunking entirely for flagged images rather than
    just deprioritizing them - a boilerplate image has no real chapter
    content to caption in the first place.
    """
    pages_by_hash = defaultdict(set)
    for d in diagrams:
        pages_by_hash[d["image_hash"]].add(d["pdf_page"])

    for d in diagrams:
        occurrence_pages = pages_by_hash[d["image_hash"]]
        d["is_boilerplate"] = (
            len(occurrence_pages) >= BOILERPLATE_MIN_OCCURRENCES
            and total_pages > 0
            and len(occurrence_pages) / total_pages >= BOILERPLATE_PAGE_FRACTION
        )
    return diagrams


def caption_diagram_image(openai_raw_client, image_bytes: bytes, fmt: str,
                           chapter_name: Optional[str] = None, topic_name: Optional[str] = None) -> Dict:
    """
    Captions one already-extracted image. `chapter_name`/`topic_name`, when
    supplied, are injected into the prompt's {context_block} (same
    enrichment pattern as embeddings/embedding_service.py::format_for_embedding) -
    a generic circuit diagram gets a caption grounded in *this chapter's*
    specific concept instead of pixels alone.

    Returns {"caption": str, "is_content": bool}. `is_content` is the model's
    own real-diagram-vs-page-furniture classification (title banner, QR
    code, decorative icon/border/logo, watermark, generic stock photo - see
    prompts/diagram_caption.txt) - confirmed live this catches real junk
    that the earlier, purely hash-based boilerplate filter
    (flag_boilerplate_images) structurally cannot: a one-off, non-repeating
    image (a chapter's title-banner art, a QR code that appears exactly
    once) was never going to hit that filter's repeated-across-many-pages
    signal no matter how its thresholds are tuned, because it only ever
    appears once. This is a second, independent filter on a different
    signal (content judgment, not byte-repetition), not a replacement for
    the boilerplate filter - a chapter can have junk of both kinds.

    On a parse failure, defaults `is_content` to True (fail open) - a
    caption that couldn't be parsed as JSON is a sign the model's response
    was malformed, not evidence the image is decorative; treating it as
    real content risks keeping one non-content image in the index, which is
    a far smaller cost than the alternative (a parse hiccup silently
    dropping a real, useful diagram).
    """
    rate_governor.reserve(rate_governor.estimate_diagram_caption_tokens())
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        mime = f"image/{fmt.lower() if fmt.lower() != 'jpg' else 'jpeg'}"
        prompt_text = _format_caption_prompt(chapter_name, topic_name)
        response = call_with_retry(lambda: openai_raw_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    # detail="low" is deliberate, not the "auto" default: this
                    # prompt only asks for a 1-2 sentence conceptual caption, not
                    # fine-detail reading, and gpt-4o-mini's "high" detail tiling
                    # costs 2,833 base + 5,667 tokens/512x512 tile per image
                    # (confirmed via OpenAI's documented pricing formula) versus
                    # a flat 85 tokens at "low" - a huge, unnecessary chunk of
                    # the TPM budget every single diagram call would otherwise
                    # be able to consume unpredictably under "auto".
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"}},
                ],
            }],
            max_tokens=150,
            response_format={"type": "json_object"},
        ))
        raw = (response.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[NEW_RAG][Stage5] Diagram caption response wasn't valid JSON, "
                            f"treating as content (fail open): {raw[:200]!r}")
            return {"caption": raw, "is_content": True}
        caption = (parsed.get("caption") or "").strip()
        is_content = parsed.get("is_content")
        if not isinstance(is_content, bool):
            is_content = True
        return {"caption": caption, "is_content": is_content}
    except Exception as e:
        logger.warning(f"[NEW_RAG][Stage5] Diagram captioning failed: {e}")
        return {"caption": "", "is_content": True}


# --- Table candidate heuristic (best-effort, see module docstring) ---

_NUMERIC_TOKEN_RE = re.compile(r"^\d+(\.\d+)?%?$")

# Bug fix (2026-08-25, found via live testing against jemh101.pdf, Class 10
# Maths "Real Numbers"): the original heuristic ("3+ tokens, 2+ of them bare
# numbers") was designed with a real data table in mind (a label followed by
# a few numeric values per row, several similar rows in sequence) but
# matches ordinary worked-math-example lines just as well - a line like
# "7 x 11 x 23 = 1771  3 x 7 x 11 x 23 = 5313" is several bare numbers
# separated by whitespace, exactly the same shape the heuristic looks for,
# despite being an equation, not tabular data. Confirmed live: this
# misclassified 5 chunks of ordinary prime-factorisation arithmetic as
# chunk_type="table", each duplicating content that was already correctly
# present in its enclosing "example"/"theorem" chunk - pure retrieval noise,
# not new information.
#
# The reliable distinguishing signal: a real table row is a label plus
# values, essentially never containing a literal equals sign or
# multiplication symbol - a worked-math line almost always contains one or
# both, since that's what doing arithmetic on a line looks like. Excluding
# any line with an equation operator is a narrow, targeted fix specific to
# the confirmed failure mode, not a general table-detection rewrite (the
# module docstring's own honest "best-effort heuristic" limitation still
# applies otherwise).
_EQUATION_OPERATOR_RE = re.compile(r"[=×÷*]")


def _line_looks_tabular(line: str) -> bool:
    if _EQUATION_OPERATOR_RE.search(line):
        return False
    tokens = line.split()
    if len(tokens) < 3:
        return False
    numeric_count = sum(1 for t in tokens if _NUMERIC_TOKEN_RE.match(t))
    return numeric_count >= 2


def detect_table_candidates(page_text: str, min_consecutive_lines: int = 3) -> List[str]:
    """
    Returns raw text blocks (as originally extracted) that look like they
    might be a table - runs of consecutive lines each containing several
    whitespace-separated numeric tokens. Heuristic only; see module
    docstring for the honest limitation here.
    """
    lines = page_text.split("\n")
    blocks = []
    current_block: List[str] = []

    for line in lines:
        if _line_looks_tabular(line):
            current_block.append(line)
        else:
            if len(current_block) >= min_consecutive_lines:
                blocks.append("\n".join(current_block))
            current_block = []
    if len(current_block) >= min_consecutive_lines:
        blocks.append("\n".join(current_block))

    return blocks
