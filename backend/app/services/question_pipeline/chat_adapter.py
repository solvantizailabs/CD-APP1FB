"""
The single seam between chat.py's live /api/smart_query endpoint and the new
question_pipeline/ engine. Built 2026-09-02 as Phase 2/3 of the orchestrator
integration (see memory: project_orchestrator_integration_checklist).

chat.py's entire dependency on the OLD orchestrator (test_runner.py's
run_orchestrator_pipeline) is exactly one call, returning a dict shaped
{"orchestrator_output": {...}, "resolved_book_uuid": ..., ...}. Everything
downstream in chat.py (SSE streaming, TTS, video-lesson trigger, caching,
personalization writes, query-id/history) only ever consumes fields off that
dict - so this adapter's whole job is: call the new run_pipeline(), then
reshape PipelineResult into that exact same dict shape, so chat.py itself
needs only a one-line call-site swap, not a rewrite.

Field-contract audit (full list of what chat.py actually reads, grepped
2026-09-02): classification, matched_subject, matched_chapter,
format_decision, text_narration, is_authorized, refusal_reason,
reformulated_query, grade_relative_difficulty (orchestrator_output); plus
resolved_book_uuid, retrieved_top10_chunks, retrieval_status,
retrieval_confidence_tier, retrieval_top_score, retrieval_retried,
retrieval_escalated_to_parent, grounding_applied, narration_before_grounding
(top-level report).

Known, deliberate gaps (not silently dropped - flagged for the record):
- grade_relative_difficulty: the old flow's single-call prompt computed this
  directly; nothing in the new pipeline computes an equivalent judgment yet.
  Returned as None - only affects an analytics/personalization write-back
  field, never the student-facing answer.
- grounding_applied/narration_before_grounding: the old flow needed a
  post-hoc grounding correction pass because it generated text_narration
  BEFORE RAG retrieval finished. The new pipeline generates AFTER Stage 4/5
  retrieval, so it's grounded by construction - there's nothing to report
  here, hence always False/None rather than an equivalent mechanism.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CURRICULUM_MATCH_TO_CLASSIFICATION = {
    "in_grade": "CURRICULUM",
    "not_in_curriculum": "GENERAL_KNOWLEDGE",
}


def run_new_orchestrator_pipeline(
    raw_query: str,
    student_profile: Dict[str, Any],
    session_id: str,
    conversation_context: List[Dict],
    uid: str,
) -> Dict[str, Any]:
    """
    Drop-in replacement for test_runner.py's run_orchestrator_pipeline(),
    extended with session_id/conversation_context/uid since the new
    pipeline needs the real conversation window (the old flow only ever saw
    student_profile["immediate_prior_turn"], a single turn baked in by
    chat.py) and a real uid (Stage 8's personal-history write requires it).
    """
    from backend.app.services.retrieval import qdrant_service
    from backend.app.services.question_pipeline.pipeline import run_pipeline
    from backend.app.services.question_pipeline.schemas import PipelineInput

    openai_client = qdrant_service.openai_client
    if openai_client is None:
        try:
            qdrant_service.initialize()
            openai_client = qdrant_service.openai_client
        except Exception as e:
            logger.warning(f"[ChatAdapter] Failed to initialize qdrant_service's openai_client: {e}")

    import os
    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # learner_context carries BOTH the fields the new pipeline's own stages
    # already read (class_name/board/language) AND the personalization
    # fields Phase 0 wired into generation.py's prompt/restyle pass
    # (response_style, quadrant, escalation_level, per_student_history,
    # tough_subjects, easy_subjects) - student_profile already has all of
    # these computed by chat.py, this just passes the same dict through.
    learner_context = dict(student_profile)
    learner_context["class_name"] = student_profile.get("class")

    pipeline_input = PipelineInput(
        student_question=raw_query,
        conversation_context=conversation_context or [],
        learner_context=learner_context,
        session_context={},
        session_id=session_id,
        uid=uid,
    )

    result = run_pipeline(pipeline_input, openai_client, model_name)

    curriculum_guess = result.curriculum_guess
    curriculum_decision = result.curriculum_decision
    rag_result = result.rag_result
    raw_retrieval = (rag_result.raw if rag_result else {}) or {}

    classification = "CURRICULUM"
    if curriculum_decision:
        classification = _CURRICULUM_MATCH_TO_CLASSIFICATION.get(curriculum_decision.curriculum_match, "GENERAL_KNOWLEDGE")

    is_authorized = result.status != "REFUSED"
    orchestrator_output = {
        "classification": classification,
        "matched_subject": curriculum_guess.subject if curriculum_guess else None,
        "matched_chapter": curriculum_guess.chapter if curriculum_guess else None,
        "format_decision": result.format_decision or "QUICK_ANSWER",
        "text_narration": result.final_answer,
        "is_authorized": is_authorized,
        "refusal_reason": result.final_answer if not is_authorized else None,
        "reformulated_query": result.query.resolved_question if result.query else raw_query,
        "grade_relative_difficulty": None,  # see module docstring - not computed in the new pipeline yet
    }

    resolved_book_uuid = (curriculum_guess.book_uuid if curriculum_guess else "") or ""

    return {
        "orchestrator_output": orchestrator_output,
        "request_id": result.request_id,  # lets chat.py attach post-hoc TTS/video data to this same log record
        "resolved_book_uuid": resolved_book_uuid,
        "retrieved_top10_chunks": rag_result.sources if rag_result else [],
        "retrieval_status": rag_result.retrieval_status if rag_result else None,
        "retrieval_confidence_tier": rag_result.confidence_tier if rag_result else None,
        "retrieval_top_score": raw_retrieval.get("top_score"),
        "retrieval_retried": raw_retrieval.get("retried", False),
        "retrieval_escalated_to_parent": raw_retrieval.get("escalated_to_parent", False),
        "grounding_applied": False,
        "narration_before_grounding": None,
        "trace": result.trace,
    }
