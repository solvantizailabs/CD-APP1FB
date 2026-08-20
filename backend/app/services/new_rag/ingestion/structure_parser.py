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
    # immediate neighbors only (one page back/forward) - extrapolating far
    # from a single anchor is exactly the failure mode this module exists to
    # avoid, so this deliberately does not run unbounded.
    first_i, first_v = anchors[0]
    if first_i == 1 and resolved[0]["textbook_page"] is None:
        resolved[0]["textbook_page"] = first_v - 1
        resolved[0]["interpolated"] = True

    last_i, last_v = anchors[-1]
    if last_i == n - 2 and resolved[n - 1]["textbook_page"] is None:
        resolved[n - 1]["textbook_page"] = last_v + 1
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


def build_raw_pages_json(pdf_path: str) -> Dict:
    """
    End-to-end Stage 1 entry point: extract -> resolve -> validate. Returns a
    dict with the resolved pages, whether validation passed, and the list of
    issues (empty if valid) - this is the object that becomes raw_pages.json
    per the plan doc, and the `is_valid` flag is what the ingestion pipeline
    checks before allowing Stage 2 (topic detection) to run at all.
    """
    from backend.app.services.new_rag.ingestion.pdf_parser import extract_raw_pages

    raw_pages = extract_raw_pages(pdf_path)
    resolved_pages = resolve_page_sequence(raw_pages)
    is_valid, issues = validate_page_sequence(resolved_pages)

    if not is_valid:
        logger.warning(f"[NEW_RAG][Stage1] Page-sequence validation FAILED for {pdf_path}: {issues}")
    else:
        logger.info(f"[NEW_RAG][Stage1] Page-sequence validated OK for {pdf_path} ({len(resolved_pages)} pages)")

    return {
        "source_pdf": pdf_path,
        "pages": resolved_pages,
        "is_valid": is_valid,
        "validation_issues": issues,
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
        anchor = t.get("anchor_text", "")
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

        resolved.append({
            "topic_name": t.get("topic_name", "Untitled"),
            "topic_type": t.get("topic_type", "topic"),
            "start_page": actual_page,
            "start_anchor": anchor,
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
    is_valid = dropped_count == 0 or (drop_ratio <= 0.15 and len(resolved) >= 3)

    if expected_unverifiable:
        logger.info(
            f"[NEW_RAG][Stage2] {len(expected_unverifiable)} formula/theorem anchor(s) "
            f"could not be verbatim-verified (expected - stacked-fraction extraction "
            f"garbling, content still present in surrounding text) - not counted toward "
            f"chapter validity: {expected_unverifiable}"
        )

    return {"topics": resolved, "is_valid": is_valid, "issues": issues + expected_unverifiable}
