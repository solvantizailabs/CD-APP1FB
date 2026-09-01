"""
Stage 4 (curriculum branch - the ONLY place curriculum_match is decided,
always from a real RAG call, never an LLM opinion) and Stage 5 (fetch,
reusing Stage 4's result rather than searching twice) of the FRD v3 flow.
See docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md sections on Stage 4/5.

Per the team's own instruction ("don't modify the completed RAG logic"),
this module only ever calls new_rag_adapter.hybrid_search_v2 - it never
touches new_rag's ingestion, retrieval, or reranking internals.

book_uuid resolution reuses resolve_book_uuid_for_subject/normalize_subject_name
from the orchestrator (already validated in production, gated on
new_rag_adapter.book_has_content()) rather than reinventing it.
"""
import logging
import re
from typing import Optional, Tuple

from backend.app.orchestrator_test.test_runner import (
    _get_classes_subjects_docs,
    normalize_subject_name,
    resolve_book_uuid_for_subject,
)
from backend.app.services.chat.session_service import session_manager
from backend.app.services.question_pipeline.schemas import (
    CurriculumDecision,
    CurriculumGuess,
    RAGResult,
    ReformulatedQuery,
)
from backend.app.services.retrieval import new_rag_adapter

logger = logging.getLogger(__name__)

# HIGH/MEDIUM confidence retrieval is what "in_grade" means - a LOW or
# INSUFFICIENT tier means no real content was found, regardless of what
# Stage 2 guessed the subject/topic to be.
_IN_GRADE_TIERS = {"HIGH", "MEDIUM"}

# Best to worst, for ranking results across multiple books - see
# _search_all_subject_books below.
_TIER_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INSUFFICIENT": 0, None: 0}


def _resolve_book_uuid(curriculum_guess: CurriculumGuess, grade: int, session_book_uuid: str = "") -> str:
    if session_book_uuid:
        return session_book_uuid
    if not curriculum_guess.subject:
        return ""
    normalized_subject = normalize_subject_name(curriculum_guess.subject)
    return resolve_book_uuid_for_subject(grade, normalized_subject) or ""


def _to_rag_result(result: dict) -> RAGResult:
    pairs = result.get("score_payload_pairs") or []
    sources = []
    context_parts = []
    for score, payload in pairs:
        text = payload.get("text") or payload.get("content") or ""
        if text:
            context_parts.append(text)
        sources.append({
            "chunk_id": payload.get("chunk_id") or payload.get("id"),
            "chunk_type": payload.get("chunk_type"),
            "chapter": payload.get("chapter_name") or payload.get("chapter"),
            "topic": payload.get("topic_name") or payload.get("topic"),
            "page": payload.get("page_number") or payload.get("page"),
            "score": score,
            "full_text": text,
            "structured_content": payload.get("structured_content"),
        })
    return RAGResult(
        context="\n\n".join(context_parts),
        sources=sources,
        confidence=result.get("top_score") or 0.0,
        confidence_tier=result.get("confidence_tier"),
        retrieval_status=result.get("status") or "unknown",
        raw=result,
    )


def summarize_chunks(sources: Optional[list]) -> list:
    """
    Compact, log-safe view of a chunk list for the pipeline log report -
    chapter/topic/page/score/text (untruncated - these are already
    bounded-size ingestion chunks, not raw documents) but WITHOUT
    structured_content, which can be large/deeply nested and isn't needed
    to answer "what evidence did retrieval find" for debugging. Used for
    both curriculum_decision's "chunks_considered" (even on a
    not_in_curriculum miss, so a bad match is diagnosable) and rag_fetch's
    "chunks_used" (whichever chunks actually went to generation, whether
    freshly searched or reused from the session cache).
    """
    return [
        {
            "chunk_id": c.get("chunk_id"),
            "chunk_type": c.get("chunk_type"),
            "chapter": c.get("chapter"),
            "topic": c.get("topic"),
            "page": c.get("page"),
            "score": c.get("score"),
            "text": c.get("full_text") or c.get("text") or "",
        }
        for c in (sources or [])
    ]


def _search_all_subject_books(search_text: str, grade: int, debug: Optional[dict] = None) -> Optional[tuple]:
    """
    Fallback for when Stage 2 couldn't guess a subject at all (found live,
    2026-08-31, via the curriculum_match_eval.json eval set: ~12% of real,
    genuinely-in-curriculum questions get an empty subject guess because the
    chapter SUMMARY text doesn't happen to use the question's exact
    vocabulary - e.g. "ozone layer" vs. the "Our Environment" chapter's more
    general summary. Without this fallback those questions silently fall to
    not_in_curriculum even though real content exists).

    Searches every subject book this grade actually has ingested content
    for (gated on new_rag_adapter.book_has_content(), same gate
    resolve_book_uuid_for_subject already uses elsewhere) and returns the
    single best result across all of them, or None if nothing worth using
    turned up anywhere. Deliberately only runs when the targeted, single-
    book search path (_resolve_book_uuid) has nothing to go on - this is a
    real, multi-book search, several times the cost of the normal path, not
    something to run on every question.
    """
    candidates = []
    for subject_id, data in _get_classes_subjects_docs(grade):
        book_uuid = data.get("book_uuid") or ""
        if not book_uuid or not new_rag_adapter.book_has_content(book_uuid):
            continue
        try:
            result = new_rag_adapter.hybrid_search_v2(
                query=search_text, book_uuid=book_uuid, class_name=str(grade), subject=subject_id, chapter_id=None,
            )
        except Exception as e:
            logger.warning(f"[QUESTION_PIPELINE][Stage4][Fallback] search failed for subject={subject_id}: {e}")
            continue
        candidates.append((subject_id, result))

    if debug is not None:
        # Every book actually tried, not just the winner - added 2026-09-01
        # so a near-miss (e.g. a real topic that scored just under HIGH) is
        # diagnosable from the log instead of only seeing "not_in_curriculum."
        debug["fallback_candidates"] = [
            {
                "subject": subject_id,
                "confidence_tier": result.get("confidence_tier"),
                "top_score": result.get("top_score"),
                "chunk_count": len(result.get("score_payload_pairs") or []),
            }
            for subject_id, result in candidates
        ]

    if not candidates:
        return None

    best_subject, best_result = max(
        candidates, key=lambda pair: (_TIER_RANK.get(pair[1].get("confidence_tier")), pair[1].get("top_score") or float("-inf"))
    )
    # Deliberately stricter than the targeted single-book path (which
    # accepts HIGH or MEDIUM) - found live via curriculum_match_eval.json
    # (2026-08-31) that an untargeted multi-book search's MEDIUM hits are
    # unreliable: three genuinely out-of-syllabus questions ("derivative of
    # a function", "mitosis and meiosis", "theory of relativity") each
    # scored a coincidental MEDIUM against an unrelated book once every
    # subject book was searched, all landing between -2.5 and -3.5 - nowhere
    # near HIGH, but enough to clear MEDIUM's -5.0 floor. The genuine fixes
    # this fallback exists for (ozone layer, spherical mirrors, coal as an
    # energy resource) all scored between +1.1 and +8.1 - comfortably HIGH.
    # Requiring HIGH here keeps the real fixes and rejects the coincidental
    # matches; MEDIUM is only trustworthy when a subject was already known,
    # which is exactly the case this fallback does NOT have.
    if best_result.get("confidence_tier") != "HIGH":
        return None
    return best_subject, best_result


def decide_curriculum(
    curriculum_guess: CurriculumGuess,
    grade: int,
    query: Optional[ReformulatedQuery] = None,
    session_book_uuid: str = "",
    decided_at_stage: str = "4",
    debug: Optional[dict] = None,
) -> CurriculumDecision:
    """
    Stage 4. Runs the actual RAG search and reads its confidence_tier - the
    only signal used. If called early from safety.py's Layer 3 (borderline
    band), decided_at_stage="1c" so the pipeline/debug record can show that
    without re-running this call at its normal position.

    debug: optional dict this stage writes "chunks_considered" into - the
    actual retrieved chunks (chapter/topic/page/score/text), even on a
    not_in_curriculum result, so a real miss (like the curriculum-summary-
    truncation bug found 2026-08-31) is diagnosable from the log instead of
    just seeing a bare confidence_tier.
    """
    search_text = (query.semantic_query if query else None) or curriculum_guess.topic or curriculum_guess.subject
    book_uuid = _resolve_book_uuid(curriculum_guess, grade, session_book_uuid)

    if not book_uuid:
        # No targeted book to search - try the multi-book fallback before
        # giving up (see _search_all_subject_books docstring).
        fallback = _search_all_subject_books(search_text, grade, debug=debug)
        if fallback is None:
            return CurriculumDecision(
                curriculum_match="not_in_curriculum",
                decided_at_stage=decided_at_stage,
                rag=RAGResult(context="", retrieval_status="skipped_no_book_uuid"),
            )
        resolved_subject, result = fallback
        # Update the guess in place so the caller's cache key / history log
        # reflect the subject the fallback actually found, not an empty one.
        curriculum_guess.subject = resolved_subject
        rag_result = _to_rag_result(result)
        curriculum_match = "in_grade" if rag_result.confidence_tier in _IN_GRADE_TIERS else "not_in_curriculum"
        if debug is not None:
            debug["chunks_considered"] = summarize_chunks(rag_result.sources)
        return CurriculumDecision(curriculum_match=curriculum_match, decided_at_stage=decided_at_stage, rag=rag_result)

    try:
        result = new_rag_adapter.hybrid_search_v2(
            query=search_text,
            book_uuid=book_uuid,
            class_name=str(grade),
            subject=curriculum_guess.subject or "",
            chapter_id=curriculum_guess.chapter_id,
        )
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Stage4] hybrid_search_v2 failed: {e}")
        return CurriculumDecision(
            curriculum_match="not_in_curriculum",
            decided_at_stage=decided_at_stage,
            rag=RAGResult(context="", retrieval_status="error", raw={"error": str(e)}),
        )

    rag_result = _to_rag_result(result)
    curriculum_match = "in_grade" if rag_result.confidence_tier in _IN_GRADE_TIERS else "not_in_curriculum"
    if debug is not None:
        debug["chunks_considered"] = summarize_chunks(rag_result.sources)
    return CurriculumDecision(curriculum_match=curriculum_match, decided_at_stage=decided_at_stage, rag=rag_result)


def _normalize_topic_field(value: Optional[str]) -> str:
    """
    Strips leading numbering/punctuation and case so e.g. "7 How do
    Organisms Reproduce?" and "How do Organisms Reproduce?" compare equal.
    Found live (2026-08-31, real data): Stage 2 inherits chapter/topic
    correctly onto a follow-up turn, but inconsistently includes a
    chapter's leading number turn-to-turn - a naive exact-string compare
    would misread that formatting difference as a real topic change.
    """
    value = (value or "").strip().lower()
    value = re.sub(r"^[\d.\s]+", "", value)
    value = re.sub(r"[^\w\s]", "", value)
    return value.strip()


def _same_topic(previous: Optional[dict], current_guess: CurriculumGuess) -> Tuple[bool, str]:
    """
    Real, deterministic same-topic check (docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md
    Stage 5: "if the resolved topic hasn't changed since the previous turn,
    reuse cached chunks... topic changed: fetch fresh") - replaces the prior
    hardcoded assumption that every follow-up is same-topic.

    Requires subject, chapter, AND topic to each agree whenever BOTH sides
    have a value for that field - a field missing on either side is not
    treated as a mismatch (Stage 2 doesn't always fill every field). An
    earlier version of this function used chapter-OR-topic, which was found
    live to be a real bug: "How do Organisms Reproduce?" is one chapter
    covering BOTH Asexual and Sexual Reproduction as distinct sections, so
    a chapter-only match let a genuine topic change (asexual -> sexual)
    wrongly reuse the wrong topic's cached chunks. Requiring ALL three
    fields to agree (where present) is what actually distinguishes two
    different topics inside the same chapter, while normalization (below)
    already handles the harmless case that motivated OR in the first place
    (a chapter name inconsistently carrying its leading number turn-to-turn
    normalizes equal, so it was never a reason to need OR at all).
    """
    if not previous:
        return False, "no cached topic metadata to compare against"

    prev_subject = _normalize_topic_field(previous.get("subject"))
    prev_chapter = _normalize_topic_field(previous.get("chapter"))
    prev_topic = _normalize_topic_field(previous.get("topic"))
    cur_subject = _normalize_topic_field(current_guess.subject)
    cur_chapter = _normalize_topic_field(current_guess.chapter)
    cur_topic = _normalize_topic_field(current_guess.topic)

    if not cur_subject or cur_subject != prev_subject:
        return False, f"subject changed ({previous.get('subject')!r} -> {current_guess.subject!r})"

    if cur_chapter and prev_chapter and cur_chapter != prev_chapter:
        return False, f"chapter changed ({previous.get('chapter')!r} -> {current_guess.chapter!r})"

    if cur_topic and prev_topic and cur_topic != prev_topic:
        return False, f"topic changed within the same chapter ({previous.get('topic')!r} -> {current_guess.topic!r})"

    return True, "subject/chapter/topic all matched (or unknown on one side)"


def fetch_for_answer(
    curriculum_decision: CurriculumDecision,
    session_id: Optional[str],
    is_followup: bool,
    curriculum_guess: CurriculumGuess,
    debug: Optional[dict] = None,
) -> RAGResult:
    """
    Stage 5. New topic (or no session): the RAG call Stage 4 already made
    IS this stage's fetch - nothing more to do, just cache it for a future
    followup. Followup on the SAME topic (now independently verified via
    _same_topic, not assumed): reuse the session's cached chunks instead of
    searching again at all - real infrastructure
    (session_manager.get_current_topic_chunks/update_topic_chunks) that
    already existed but had zero call sites anywhere in the live app
    before this. Followup on a DIFFERENT topic: falls through to Stage 4's
    already-fresh result below, same as a new topic - no extra RAG call
    needed since Stage 4 always searches against THIS turn's own resolved
    query/subject regardless of follow-up status.

    debug: optional dict this stage writes chunk-cache provenance into
    (previous_topic/current_topic/same_topic/same_topic_reason plus the
    existing chunk_cache_action fields) for the pipeline log report.
    """
    previous_meta = session_manager.get_current_topic_meta(session_id) if (is_followup and session_id) else None
    same_topic, reason = _same_topic(previous_meta, curriculum_guess) if is_followup else (False, "not a follow-up")

    if debug is not None:
        debug["same_topic"] = same_topic
        debug["same_topic_reason"] = reason
        debug["previous_topic"] = previous_meta
        debug["current_topic"] = {
            "subject": curriculum_guess.subject, "chapter": curriculum_guess.chapter, "topic": curriculum_guess.topic,
        }

    if same_topic and session_id:
        cached_chunks = session_manager.get_current_topic_chunks(session_id)
        if cached_chunks:
            context = "\n\n".join(c.get("full_text") or c.get("text") or "" for c in cached_chunks)
            if debug is not None:
                debug["chunk_cache_action"] = "reused_session_cache"
                debug["chunk_cache_chunk_count"] = len(cached_chunks)
                debug["chunk_cache_note"] = f"Reused current_topic_chunks - verified same topic ({reason})."
                debug["chunks_used"] = summarize_chunks(cached_chunks)
            return RAGResult(
                context=context,
                sources=cached_chunks,
                confidence_tier=curriculum_decision.rag.confidence_tier if curriculum_decision.rag else None,
                retrieval_status="reused_session_cache",
            )

    rag = curriculum_decision.rag or RAGResult(context="", retrieval_status="not_attempted")
    if session_id and rag.sources:
        session_manager.update_topic_chunks(session_id, rag.sources, topic_meta={
            "subject": curriculum_guess.subject, "chapter": curriculum_guess.chapter, "topic": curriculum_guess.topic,
        })
    if debug is not None:
        debug["chunk_cache_action"] = "fresh_search_cached" if (session_id and rag.sources) else "fresh_search_not_cached"
        debug["chunk_cache_chunk_count"] = len(rag.sources)
        debug["chunks_used"] = summarize_chunks(rag.sources)
        if is_followup and not same_topic:
            debug["chunk_cache_note"] = f"Follow-up on a DIFFERENT topic ({reason}) - used Stage 4's fresh result, cache overwritten."
    return rag
