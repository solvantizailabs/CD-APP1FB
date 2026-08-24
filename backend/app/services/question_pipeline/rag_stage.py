"""
Stage 8 (spec sections 2, 10): call the EXISTING RAG API. Per section 1
("We should not modify the completed RAG logic") and section 16 ("Rewrite
the existing RAG unnecessarily"), this module only calls
new_rag_adapter.hybrid_search_v2 - it does not touch new_rag's ingestion,
retrieval, or reranking internals.

book_uuid resolution reuses resolve_book_uuid_for_subject/normalize_subject_name
from the orchestrator, since that logic (fuzzy subject matching + gating on
new_rag_adapter.book_has_content) is already validated in production and the
spec explicitly says not to reinvent RAG-adjacent logic that already works.
"""
import logging

from backend.app.orchestrator_test.test_runner import (
    normalize_subject_name,
    resolve_book_uuid_for_subject,
)
from backend.app.services.question_pipeline.schemas import (
    CurriculumContext,
    RAGResult,
    ReformulatedQuery,
)
from backend.app.services.retrieval import new_rag_adapter

logger = logging.getLogger(__name__)


def resolve_book_and_chapter(curriculum: CurriculumContext, session_book_uuid: str = "") -> CurriculumContext:
    """Fills in book_uuid on the curriculum context, preferring an explicit
    session-provided book_uuid (student already reading a specific book) over
    a fresh subject-name resolution."""
    if session_book_uuid:
        curriculum.book_uuid = session_book_uuid
        return curriculum

    if not curriculum.class_name or not curriculum.subject:
        return curriculum

    try:
        grade = int("".join(ch for ch in str(curriculum.class_name) if ch.isdigit()) or "0")
    except ValueError:
        grade = 0

    if grade <= 0:
        return curriculum

    normalized_subject = normalize_subject_name(curriculum.subject)
    book_uuid = resolve_book_uuid_for_subject(grade, normalized_subject)
    curriculum.book_uuid = book_uuid or ""
    return curriculum


def call_rag(query: ReformulatedQuery, curriculum: CurriculumContext) -> RAGResult:
    if not curriculum.book_uuid:
        return RAGResult(
            context="",
            retrieval_status="skipped_no_book_uuid",
            raw={},
        )

    try:
        result = new_rag_adapter.hybrid_search_v2(
            query.semantic_query or query.resolved_question,
            curriculum.book_uuid,
            class_name=curriculum.class_name,
            subject=curriculum.subject,
            chapter_id=curriculum.chapter_id,
        )
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][RAG] hybrid_search_v2 failed: {e}")
        return RAGResult(context="", retrieval_status="error", raw={"error": str(e)})

    pairs = result.get("score_payload_pairs") or []
    sources = []
    context_parts = []
    for score, payload in pairs:
        text = payload.get("text") or payload.get("content") or ""
        if text:
            context_parts.append(text)
        sources.append({
            "chunk_id": payload.get("chunk_id") or payload.get("id"),
            "chapter": payload.get("chapter_name") or payload.get("chapter"),
            "topic": payload.get("topic"),
            "page": payload.get("page"),
            "score": score,
        })

    return RAGResult(
        context="\n\n".join(context_parts),
        sources=sources,
        confidence=result.get("top_score") or 0.0,
        confidence_tier=result.get("confidence_tier"),
        retrieval_status=result.get("status") or "unknown",
        raw=result,
    )
