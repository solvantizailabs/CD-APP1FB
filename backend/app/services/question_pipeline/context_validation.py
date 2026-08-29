"""
Stage 9 (spec section 10 "Context Validation" / section 12): decide whether
the retrieved RAG context is good enough to hand to the LLM at all.
Deterministic - no LLM call, no re-ranking, just a gate on what RAG already
told us (status/confidence_tier), mirroring the confidence_tier contract
new_rag_adapter.hybrid_search_v2 already returns.
"""
from backend.app.services.question_pipeline.schemas import ContextValidationResult, RAGResult

_INSUFFICIENT_TIERS = {"LOW", "NONE"}


def validate_context(rag: RAGResult) -> ContextValidationResult:
    if rag.retrieval_status in ("skipped_no_book_uuid", "error", "unknown"):
        return ContextValidationResult(is_sufficient=False, reason=f"retrieval_status={rag.retrieval_status}")

    if not rag.context.strip():
        return ContextValidationResult(is_sufficient=False, reason="empty retrieved context")

    if rag.confidence_tier and rag.confidence_tier.upper() in _INSUFFICIENT_TIERS:
        return ContextValidationResult(is_sufficient=False, reason=f"confidence_tier={rag.confidence_tier}")

    return ContextValidationResult(is_sufficient=True, reason=f"confidence_tier={rag.confidence_tier}")
