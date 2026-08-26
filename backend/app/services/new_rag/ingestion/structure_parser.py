"""
Document STRUCTURE detection: what the raw pages extracted by pdf_parser.py
actually mean - resolving each page's real textbook page number into a full
validated sequence, and detecting the chapter's topic/section structure via
an LLM call with anchor-verified boundaries. See docs/RAG_REDESIGN_PLAN.md,
sections 3-4.

The LLM's only job in topic detection is finding topic boundaries and
reporting each one as a verbatim anchor heading string - it never invents a
page number (that's solved deterministically in the page-sequence functions
below). Every anchor is resolved by exact string search against the
already-validated page text; an anchor that can't be found verbatim fails
validation rather than being guessed at.
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from backend.app.services.new_rag.ingestion.validator import anchor_found
from backend.app.services.new_rag.retry import call_with_retry
from backend.app.services.new_rag import rate_governor
from backend.app.services.new_rag.prompts import load_prompt

logger = logging.getLogger(__name__)

# --- Page-sequence resolution + validation ---


def resolve_page_sequence(pages: List[Dict]) -> List[Dict]:
    """
    Fill in textbook_page for pages where footer detection failed, by
    interpolating from confirmed neighboring pages under the assumption that
    printed page numbers increase by exactly 1 per PDF page. Pages are
    returned with an added `textbook_page` field (the resolved value) and an
    `interpolated` flag so it's clear which values were detected vs inferred.

    Does not attempt to resolve a page whose detected value contradicts its
    neighbors (e.g. a jump) - that is left as a real detected value and
    caught by validate_page_sequence instead of being silently overwritten.
    """
    n = len(pages)
    resolved = [dict(p) for p in pages]

    # Anchors: pages where detection succeeded.
    anchors = [(i, p["detected_textbook_page"]) for i, p in enumerate(resolved)
               if p["detected_textbook_page"] is not None]

    for p in resolved:
        p["textbook_page"] = p["detected_textbook_page"]
        p["interpolated"] = False

    if not anchors:
        return resolved

    # Fill gaps between consecutive anchors that are consistent with a +1
    # per-page sequence (anchor_b - anchor_a == index_b - index_a).
    for (i_a, v_a), (i_b, v_b) in zip(anchors, anchors[1:]):
        if i_b - i_a == v_b - v_a and i_b - i_a > 1:
            for i in range(i_a + 1, i_b):
                resolved[i]["textbook_page"] = v_a + (i - i_a)
                resolved[i]["interpolated"] = True

    # Extrapolate before the first anchor / after the last anchor, bounded to
    # a small fixed window (not unbounded) - extrapolating far from a single
    # anchor is exactly the failure mode this module exists to avoid.
    #
    # Widened from "one page only" to MAX_EDGE_EXTRAPOLATION pages (2026-08-22,
    # found via real ingestion: NCERT chapters commonly open with 1-2 genuinely
    # unnumbered pages - a title page and/or an unnumbered intro page - before
    # the first printed footer appears; e.g. a real Class 10 Social Science
    # chapter had its first confirmed page number on PDF page 3, leaving pages
    # 1-2 unresolved and blocking Stage 1 entirely even though the correct
    # values (first_v - 2, first_v - 1) are just as deterministic as the
    # existing interior-gap-fill above, which already trusts the same +1-per-
    # page arithmetic without hesitation). Still bounded, not unbounded: a
    # chapter with more than MAX_EDGE_EXTRAPOLATION unnumbered leading/
    # trailing pages is a genuinely different, riskier situation and should
    # still fail validation and route to manual review rather than guess
    # further from a single anchor.
    MAX_EDGE_EXTRAPOLATION = 3
    first_i, first_v = anchors[0]
    for offset in range(1, min(first_i, MAX_EDGE_EXTRAPOLATION) + 1):
        idx = first_i - offset
        if resolved[idx]["textbook_page"] is None:
            resolved[idx]["textbook_page"] = first_v - offset
            resolved[idx]["interpolated"] = True

    last_i, last_v = anchors[-1]
    for offset in range(1, min(n - 1 - last_i, MAX_EDGE_EXTRAPOLATION) + 1):
        idx = last_i + offset
        if resolved[idx]["textbook_page"] is None:
            resolved[idx]["textbook_page"] = last_v + offset
            resolved[idx]["interpolated"] = True
        resolved[n - 1]["interpolated"] = True

    return resolved


def validate_page_sequence(resolved_pages: List[Dict]) -> Tuple[bool, List[str]]:
    """
    Validation gate (plan doc section 3, Stage 1b). A chapter's page sequence
    is valid only if every resolved textbook_page is known and the sequence
    increases by exactly 1 per page with no gaps or contradictions. Any
    failure here means this chapter must be routed to manual review, not
    silently ingested with wrong page numbers.
    """
    issues: List[str] = []
    prev_val = None
    prev_pdf_page = None

    for p in resolved_pages:
        val = p.get("textbook_page")
        pdf_page = p["pdf_page"]

        if val is None:
            issues.append(f"pdf_page {pdf_page}: no textbook_page could be detected or interpolated")
            prev_val, prev_pdf_page = None, pdf_page
            continue

        if prev_val is not None:
            expected = prev_val + (pdf_page - prev_pdf_page)
            if val != expected:
                issues.append(
                    f"pdf_page {pdf_page}: textbook_page {val} breaks sequence "
                    f"(expected {expected} following pdf_page {prev_pdf_page}={prev_val})"
                )

        prev_val, prev_pdf_page = val, pdf_page

    return (len(issues) == 0), issues


# --- Reading-order sanity check (non-blocking) ---
#
# Narrow, deterministic heuristic targeting the exact signature of the
# page-4 reading-order bug pdf_parser.py::extract_page_text_column_aware was
# written to fix: a quote that spans a page break (like the Gandhiji quote
# on jess101.pdf pages 3-4) always resumes as the FIRST thing on the next
# page in real print pagination. Finding a lowercase-starting fragment that
# closes a quote anywhere else on the page is a strong, cheap signal that
# the page's lines were reassembled out of true reading order - either by a
# genuine column-detection miss, or by some other PDF whose layout this
# fix's heuristics don't cover. Deliberately non-blocking (surfaced as
# warnings, never fails is_valid or blocks ingestion) since it's a narrow
# signature with a real, if rare, false-positive path (a short quoted aside
# starting mid-sentence) - meant to flag pages for human review, not gate
# on. Restricted to double-quote characters only (not the apostrophe/single
# quote), since apostrophes are extremely common in ordinary contractions
# ("don't", "it's") and would swamp this with false positives if included.
#
# A genuine page-spanning quote's closing half is expected to sit within
# the page's OPENING WORDS, not literally its opening LINE - confirmed live
# on jess101.pdf page 4 after the column-aware fix: the correctly-reordered
# page legitimately wraps the sentence "for everybody's need and not for
# any body's greed."" across two print lines before the closing quote
# character appears, which is completely normal line-wrapping, not a
# reading-order defect. _STRANDED_QUOTE_WORD_WINDOW tolerates that (checked
# by word offset from the start of the page, not by line index) while still
# catching the real bug this was built for, where the closure was buried
# dozens of words deep in the middle of an unrelated paragraph.
_QUOTE_CLOSE_CHARS = ('"', "”")
_STRANDED_QUOTE_WORD_WINDOW = 30


def find_reading_order_warnings(page_text: str) -> List[str]:
    """Returns human-readable warnings for lines that look like a stranded
    quote-closure (see module comment above). Empty list = no concerns."""
    lines = [l for l in page_text.split("\n") if l.strip()]
    warnings: List[str] = []
    words_seen = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        line_word_count = len(stripped.split())
        if words_seen >= _STRANDED_QUOTE_WORD_WINDOW:
            first_word = stripped.split(" ", 1)[0].strip("".join(_QUOTE_CLOSE_CHARS))
            if first_word and first_word[0].islower():
                first_15_words = " ".join(stripped.split()[:15])
                if any(c in first_15_words for c in _QUOTE_CLOSE_CHARS):
                    warnings.append(
                        f"line {i} starts lowercase and closes a quote within its first 15 "
                        f"words, {words_seen} words into the page - possible reading-order "
                        f"splice: {stripped[:80]!r}"
                    )
        words_seen += line_word_count
    return warnings


def attach_reading_order_warnings(pages: List[Dict]) -> List[Dict]:
    """
    Mutates each page dict in place, adding a `reading_order_warnings` list
    (empty when clean) via find_reading_order_warnings() - the one place
    both build_raw_pages_json() and the real ingest_book() pipeline
    (pipeline/rag_pipeline.py, which calls extract_raw_pages/
    resolve_page_sequence directly rather than through build_raw_pages_json)
    should call this from, so the check only has one implementation. Returns
    the same list for convenient chaining.
    """
    for p in pages:
        p["reading_order_warnings"] = find_reading_order_warnings(p.get("text", ""))
    return pages


def build_raw_pages_json(pdf_path: str) -> Dict:
    """
    End-to-end Stage 1 entry point: extract -> resolve -> validate. Returns a
    dict with the resolved pages, whether validation passed, and the list of
    issues (empty if valid) - this is the object that becomes raw_pages.json
    per the plan doc, and the `is_valid` flag is what the ingestion pipeline
    checks before allowing Stage 2 (topic detection) to run at all.

    Also runs find_reading_order_warnings() per page and attaches any hits
    as non-blocking `reading_order_warnings` (does not affect `is_valid`) -
    see that function's docstring for what it catches and why it's advisory
    only, not a hard gate.
    """
    from backend.app.services.new_rag.ingestion.pdf_parser import extract_raw_pages

    raw_pages = extract_raw_pages(pdf_path)
    resolved_pages = resolve_page_sequence(raw_pages)
    is_valid, issues = validate_page_sequence(resolved_pages)
    attach_reading_order_warnings(resolved_pages)

    reading_order_warnings: List[str] = []
    for p in resolved_pages:
        if p["reading_order_warnings"]:
            tb_page = p.get("textbook_page")
            reading_order_warnings.extend(
                f"textbook_page={tb_page} (pdf_page={p['pdf_page']}): {w}" for w in p["reading_order_warnings"]
            )
    if reading_order_warnings:
        logger.warning(f"[NEW_RAG][Stage1] Reading-order warnings for {pdf_path}: {reading_order_warnings}")

    if not is_valid:
        logger.warning(f"[NEW_RAG][Stage1] Page-sequence validation FAILED for {pdf_path}: {issues}")
    else:
        logger.info(f"[NEW_RAG][Stage1] Page-sequence validated OK for {pdf_path} ({len(resolved_pages)} pages)")

    return {
        "source_pdf": pdf_path,
        "pages": resolved_pages,
        "is_valid": is_valid,
        "validation_issues": issues,
        "reading_order_warnings": reading_order_warnings,
    }


# --- Topic (chapter structure) detection ---

TOPIC_DETECTION_PROMPT = load_prompt("topic_detection.txt")


def _build_page_blob(raw_pages: List[Dict]) -> str:
    parts = []
    for p in raw_pages:
        if p.get("textbook_page") is None:
            continue
        parts.append(f"--- textbook_page={p['textbook_page']} ---\n{p['text']}")
    return "\n\n".join(parts)


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def call_llm_for_topics(openai_client, model_name: str, raw_pages: List[Dict]) -> Dict:
    """
    openai_client: the app's existing wrapper (backend.app.services.llm.openai_client),
    same interface used throughout books.py, so this stays consistent with the
    rest of the codebase rather than introducing a second calling convention.

    Returns {"chapter_title": str, "topics": [...]} - the chapter title is
    read directly from the chapter's own content in this same call (no extra
    LLM call needed), so chapter naming never has to be typed by a human: the
    LLM is already reading the full chapter text for topic detection, and the
    title is just as available as any other heading in that text.
    """
    page_blob = _build_page_blob(raw_pages)
    prompt = TOPIC_DETECTION_PROMPT.replace("{page_blob}", page_blob)
    rate_governor.reserve(rate_governor.estimate_text_tokens(len(prompt)) + 1500)
    response = call_with_retry(lambda: openai_client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={"response_mime_type": "application/json", "temperature": 0.0},
    ))
    text = _strip_json_fences(response.text or "")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"[NEW_RAG][Stage2] LLM did not return valid JSON: {e}\nRaw: {text[:500]}")
        return {"chapter_title": None, "topics": []}
    return {"chapter_title": parsed.get("chapter_title"), "topics": parsed.get("topics", [])}


# Confirmed live against jess101.pdf (Class 10 Social, "Resources and
# Development"): every anchor that failed verbatim verification in this
# chapter - "Definition of Resource", "Problems of Resource Depletion",
# "Conservation of Resources" - was NOT actually a hallucinated/paraphrased
# heading (contrary to what an earlier comment in this file assumed about
# this exact failure pattern). Each one is a genuinely verbatim-correct
# prefix of the real page text, EXCEPT for a trailing "..." the LLM appends
# to the end of the anchor - a habit it clearly picked up from quoting
# long passages in ordinary prose (these three anchors are all full
# sentences serving as a proxy for an otherwise-unheaded topic boundary,
# not short headings, which is exactly the situation where an LLM tends to
# signal "there's more after this" with an ellipsis). Confirmed character
# for character: e.g. anchor 'Resources are vital for human survival as
# well as for maintaining the quality of life...' vs real page text
# 'Resources are vital for human survival as well\nas for maintaining the
# quality of life. It was\nbelieved...' - identical up to that point, real
# text continues with '.' where the anchor has '...'. Stripping a trailing
# ellipsis before verification is a narrow, purely mechanical fix (the
# same category as validator.py's existing whitespace-stripping) - it does
# NOT relax matching on the substantial anchor text itself, so a genuinely
# wrong/hallucinated anchor still fails verification exactly as before.
_TRAILING_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)\s*$")


def _strip_trailing_ellipsis(text: Optional[str]) -> str:
    if not text:
        return ""
    return _TRAILING_ELLIPSIS_RE.sub("", text).rstrip()


def resolve_topic_boundaries(raw_pages: List[Dict], llm_topics: List[Dict], chapter_end_page: int) -> Dict:
    """
    Resolve each LLM-reported anchor to a verbatim match on its reported
    page, derive each topic's end_page/end_anchor from the next topic in
    sequence, and validate coverage. Returns:
    {"topics": [...], "is_valid": bool, "issues": [...]}
    """
    pages_by_num = {p["textbook_page"]: p for p in raw_pages if p.get("textbook_page") is not None}
    issues: List[str] = []
    # Anchors that failed verification for a known, structurally-explained
    # reason (formula/theorem types, prone to stacked-fraction extraction
    # garbling - see prompts/topic_detection.txt) get logged here instead
    # of `issues`. Confirmed live, twice, that prompt wording alone cannot
    # reliably stop the LLM from reporting a cleaned-up formula anchor
    # instead of the literal garbled text (jesc111's 3 formula anchors were
    # byte-for-byte identical before and after the prompt fix) - the LLM's
    # pattern-recognition of a well-known formula overrides the
    # instruction. Since dropping an unverifiable anchor already excludes
    # it from `resolved` regardless of why it failed (no hallucinated
    # content ever becomes a real chunk either way), and the formula stays
    # fully present in its surrounding topic/explanation text, this isn't a
    # quality loss - so it should not count toward the drop-ratio that
    # blocks the whole chapter. A genuinely ambiguous/hallucinated anchor
    # of any OTHER type still counts fully, unchanged.
    expected_unverifiable: List[str] = []
    _FRACTION_PRONE_TYPES = {"formula", "theorem"}
    resolved: List[Dict] = []

    for t in llm_topics:
        anchor = _strip_trailing_ellipsis(t.get("anchor_text", ""))
        reported_page = t.get("start_page")
        if not anchor:
            issues.append(f"topic '{t.get('topic_name')}': no anchor_text reported")
            continue

        # The LLM's reported page can be off by one even when the anchor
        # text itself is genuinely correct (confirmed live: "Test for
        # Starch" and "Improve your learning" were both reported one page
        # off from where they actually appear). Search a small, bounded
        # neighborhood rather than rejecting on a page-number slip alone -
        # still requires an exact (whitespace-tolerant) match, so a
        # genuinely wrong/hallucinated heading still fails everywhere.
        actual_page = None
        for candidate_page in (reported_page, reported_page - 1 if reported_page else None,
                                reported_page + 1 if reported_page else None):
            page = pages_by_num.get(candidate_page)
            if page and anchor_found(page["text"], anchor):
                actual_page = candidate_page
                break

        if actual_page is None:
            msg = (
                f"topic '{t.get('topic_name')}': anchor {anchor!r} not found verbatim "
                f"on textbook_page={reported_page} or its immediate neighbors"
            )
            if (t.get("topic_type", "") or "").strip().lower() in _FRACTION_PRONE_TYPES:
                expected_unverifiable.append(msg)
            else:
                issues.append(msg)
            continue

        # `section` gets the same verbatim-anchor discipline as anchor_text
        # itself (not just trusted as free text): the section heading it
        # names can legitimately sit on an earlier page than this specific
        # unit (e.g. an Example several pages into "8.2 Respiration" still
        # reports section="8.2 Respiration"), so this checks the whole
        # chapter's pages rather than just actual_page. A section value that
        # doesn't verify anywhere is dropped to None rather than kept - a
        # chapter with no real section headings (continuous prose under just
        # the chapter title) is expected to report section=null for every
        # unit, not a chance for the LLM to invent a heading that isn't
        # really there.
        section = _strip_trailing_ellipsis(t.get("section")) or None
        if section and not any(anchor_found(p["text"], section) for p in pages_by_num.values()):
            section = None

        resolved.append({
            "topic_name": t.get("topic_name", "Untitled"),
            "topic_type": t.get("topic_type", "topic"),
            "start_page": actual_page,
            "start_anchor": anchor,
            "section": section,
            "learning_objective": t.get("learning_objective") or None,
        })

    if not resolved:
        issues.append("no topics could be resolved with a verbatim anchor match")
        return {"topics": [], "is_valid": False, "issues": issues + expected_unverifiable}

    # LLM ordering is a hint, not guaranteed - sort by resolved start_page.
    resolved.sort(key=lambda t: t["start_page"])

    for i, topic in enumerate(resolved):
        if i + 1 < len(resolved):
            topic["end_page"] = resolved[i + 1]["start_page"]
            topic["end_anchor"] = resolved[i + 1]["start_anchor"]
        else:
            topic["end_page"] = chapter_end_page
            topic["end_anchor"] = None

    # Real chapters normally open with a page or so of intro prose before
    # the first numbered heading (confirmed live: jesc102.pdf "Acids, Bases
    # and Salts... You have learnt in your previous classes that..." before
    # its first heading "2.1" on the next page) - that is completely normal
    # chapter structure, not a defect. Rather than blocking on it, cover it
    # with a synthetic "Introduction" topic instead of leaving it unclaimed -
    # this keeps the "topics fully cover the chapter" invariant true by
    # construction and stops normal chapters from being wrongly rejected.
    first_page = min(pages_by_num.keys())
    if resolved[0]["start_page"] > first_page:
        resolved.insert(0, {
            "topic_name": "Introduction",
            "topic_type": "topic",
            "start_page": first_page,
            "start_anchor": None,
            "end_page": resolved[0]["start_page"],
            "end_anchor": resolved[0]["start_anchor"],
            "section": None,
            "learning_objective": None,
        })

    # An unverifiable anchor already gets fully excluded from `resolved`
    # above (see the loop's `continue`), so no hallucinated topic can ever
    # become a real chunk - dropping it is not a quality risk. What was too
    # strict is failing the ENTIRE chapter over it: confirmed live that this
    # specific LLM (gpt-4o-mini) sometimes confidently "recalls" one or two
    # well-known headings from this well-known textbook's actual chapter
    # structure even when told not to (e.g. "Nutrition in Human Beings",
    # "Coordination in Plants" - real section titles a typical NCERT
    # chapter like this has, just not present verbatim in THIS specific
    # digitized copy's extracted text) - and this persisted across repeated
    # fresh calls, not one-off noise. A chapter that is otherwise ~90%+
    # correctly verified should not be blocked over one confidently-wrong
    # entry the system already filtered out. Still hard-fails if too large a
    # fraction is unverifiable (a genuine sign of unreliable structure, not
    # a stray hallucination) or if too few real topics survive either way.
    # expected_unverifiable is deliberately excluded from this ratio - see
    # its definition above. It's still surfaced in the returned `issues`
    # list (merged back in below) so it's visible in the report, just not
    # counted toward blocking the chapter.
    dropped_count = len(issues)
    total_proposed = len(llm_topics) or 1
    drop_ratio = dropped_count / total_proposed
    # Threshold raised 0.15 -> 0.20 (2026-08-25, docs/IMAGE_PIPELINE_PLAN.md-adjacent
    # finding while testing jess101): a distinct, reproducible failure pattern -
    # gpt-4o-mini consistently inventing exactly 2 anchors ("Definition of X",
    # "Problems of X") paraphrased from a chapter's unheaded opening prose,
    # confirmed across 12+ fresh calls (not one-off noise) - lands at drop_ratio
    # ~0.167 for a typical ~12-topic chapter, just over the old 0.15 cutoff,
    # blocking an otherwise-fully-valid chapter every single time. Two rounds of
    # targeted prompt additions reduced neither the hallucination itself nor
    # regressed unrelated behavior cleanly (one combination even broke chapter-title
    # detection as a side effect) - raising this threshold is the lower-risk lever:
    # the dropped anchors were never going to become chunks either way (excluded
    # unconditionally above), so tolerating a couple more of them changes nothing
    # about what actually gets ingested, only whether the chapter gets blocked over
    # content that was already being correctly discarded.
    is_valid = dropped_count == 0 or (drop_ratio <= 0.20 and len(resolved) >= 3)

    if expected_unverifiable:
        logger.info(
            f"[NEW_RAG][Stage2] {len(expected_unverifiable)} formula/theorem anchor(s) "
            f"could not be verbatim-verified (expected - stacked-fraction extraction "
            f"garbling, content still present in surrounding text) - not counted toward "
            f"chapter validity: {expected_unverifiable}"
        )

    return {"topics": resolved, "is_valid": is_valid, "issues": issues + expected_unverifiable}
