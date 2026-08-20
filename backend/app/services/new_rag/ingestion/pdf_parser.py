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
import io
import re
import logging
from typing import Dict, List, Optional

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


def extract_raw_pages(pdf_path: str) -> List[Dict]:
    """
    Extract raw text and a per-page detected textbook_page for every page in
    the PDF. `textbook_page` is None here where detection failed on that
    specific page - structure_parser.py::resolve_page_sequence fills those in
    by interpolation, it is not done inline here so the raw detection result
    stays inspectable. Text is cleaned with collapse_repeated_runs() before
    anything else sees it, since the duplication artifact it fixes otherwise
    corrupts page-number detection, topic-heading anchors, AND general chunk
    text quality all at once - cleaning once here benefits every downstream
    stage.
    """
    reader = PdfReader(pdf_path)
    pages = []
    for idx, page in enumerate(reader.pages):
        text = collapse_repeated_runs(page.extract_text() or "")
        pages.append({
            "pdf_page": idx + 1,
            "text": text,
            "detected_textbook_page": detect_textbook_page_number(text),
        })
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
                            min_size_px: int = 110) -> List[Dict]:
    """
    Extracts embedded images from the given PDF page range - deterministic,
    no LLM call, no caption yet. Split out from captioning (caption_diagram_image
    below) specifically so the caller can resolve each image's enclosing
    topic BEFORE captioning it and pass that context into the prompt - doing
    captioning inside this same extraction loop (the original design) meant
    every caption was generated in isolation from pixels alone, since the
    caller only learns which topic a diagram belongs to after extraction
    finishes. Skips tiny images (likely decorative icons/rules, not real
    diagrams) via the min_size_px filter.
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
            diagrams.append({
                "pdf_page": pdf_page_idx + 1,
                "image_name": f"{img_idx_on_page}_{img.name}",
                "image_bytes": image_bytes,
                "image_format": fmt,
                "size": pil_img.size,
            })

    return diagrams


def caption_diagram_image(openai_raw_client, image_bytes: bytes, fmt: str,
                           chapter_name: Optional[str] = None, topic_name: Optional[str] = None) -> str:
    """
    Captions one already-extracted image. `chapter_name`/`topic_name`, when
    supplied, are injected into the prompt's {context_block} (same
    enrichment pattern as embeddings/embedding_service.py::format_for_embedding) -
    a generic circuit diagram gets a caption grounded in *this chapter's*
    specific concept instead of pixels alone.
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
            max_tokens=100,
        ))
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[NEW_RAG][Stage5] Diagram captioning failed: {e}")
        return ""


# --- Table candidate heuristic (best-effort, see module docstring) ---

_NUMERIC_TOKEN_RE = re.compile(r"^\d+(\.\d+)?%?$")


def _line_looks_tabular(line: str) -> bool:
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
