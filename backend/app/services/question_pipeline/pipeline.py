"""
Full FRD v3 pipeline, in the real sequence (docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md):

  Stage 1a/1b (safety.py) -> Stage 2 (understanding.py) ->
  Stage 1c if Layer 2 was borderline (safety.py, calls into Stage 4 early) ->
  Stage 3 (cache check) -> Stage 4 (curriculum decision, real RAG call,
  reused from Stage 1c if it already ran) -> Stage 5 (fetch, reusing Stage 4
  or the session's cached same-topic chunks) -> Stage 6 (format + generate,
  or web search for non-curriculum current-events questions) -> Stage 7/8
  (deliver/save - handled by the caller, e.g. chat.py, for now; this module
  returns everything needed for that).

Standalone entry point (`run_pipeline`) - not yet wired into
backend/app/api/routes/chat.py (that cutover is a separate, later step per
the project's own no-live-cutover-without-validation-first constraint).

Observability (2026-08-31): every stage is timed via StageTimer, every LLM
call captures real tokens/cost via observability/llm_call.py, and a login
session tracker_id is resolved per uid (observability/tracker_session.py).
The full structured record is assembled and persisted (Firestore + local
JSON mirror) at every exit point via `finish()` below - see
observability/log_store.py. This never changes what the student sees or
any stage's own decision - it only observes and records it.
"""
import datetime
import logging
from typing import Dict, Optional

from backend.app.services.question_pipeline import (
    generation,
    routing,
    safety,
    understanding,
)
from backend.app.services.question_pipeline import rag_stage
from backend.app.services.question_pipeline.observability import log_store, tracker_session
from backend.app.services.question_pipeline.observability.stage_timer import StageTimer
from backend.app.services.question_pipeline.schemas import PipelineInput, PipelineResult

logger = logging.getLogger(__name__)


def _grade_from_learner_context(learner_context: Dict) -> int:
    raw = learner_context.get("class_name") or learner_context.get("class") or 7
    try:
        return int("".join(ch for ch in str(raw) if ch.isdigit()) or "0") or 7
    except ValueError:
        return 7


def run_pipeline(
    pipeline_input: PipelineInput,
    openai_client,
    model_name: str,
) -> PipelineResult:
    trace = []
    timer = StageTimer()
    llm_calls = []
    request_id = log_store.new_request_id()
    tracker = tracker_session.get_or_create_tracker(pipeline_input.uid)
    grade = _grade_from_learner_context(pipeline_input.learner_context)
    board = pipeline_input.learner_context.get("board", "")
    language = pipeline_input.learner_context.get("language", "")

    def finish(result: PipelineResult) -> PipelineResult:
        """Assembles and persists the structured log record for this
        request, exactly once, regardless of which exit point returned.
        Logging failures are swallowed inside log_store - this never
        affects what's returned to the caller."""
        record = {
            "request_id": request_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
            "tracker_id": tracker["tracker_id"] if tracker else None,
            "tracker_hours_left": tracker["hours_left"] if tracker else None,
            "book_session_id": pipeline_input.session_id,
            "uid": pipeline_input.uid,
            "raw_question": pipeline_input.student_question,
            "resolved_question": (result.resolved.resolved_question if result.resolved else pipeline_input.student_question),
            "grade": grade, "board": board, "language": language,
            "status": result.status,
            "format_decision": result.format_decision,
            "route": result.routing.route if result.routing else None,
            "is_follow_up": result.resolved.used_follow_up if result.resolved else False,
            "resolution_reason": result.resolved.resolution_reason if result.resolved else None,
            "subject": result.curriculum_guess.subject if result.curriculum_guess else "",
            "chapter_name": result.curriculum_guess.chapter if result.curriculum_guess else "",
            "topic": result.curriculum_guess.topic if result.curriculum_guess else "",
            "final_answer": result.final_answer,
            "stages": timer.as_list(),
            "total_duration_ms": timer.total_duration_ms,
            "llm_calls": [
                {
                    "stage": c.stage, "model": c.model,
                    "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                    "total_tokens": c.total_tokens, "cost": c.cost,
                    "duration_ms": c.duration_ms, "token_source": c.token_source,
                    "prompt_sent": c.prompt_sent, "raw_output": c.text, "error": c.error, **c.extra,
                }
                for c in llm_calls
            ],
            "total_tokens": sum(c.total_tokens for c in llm_calls),
            "total_cost": round(sum(c.cost for c in llm_calls), 6),
            "trace": trace,
        }
        log_store.save_pipeline_log(record)
        result.request_id = request_id
        return result

    # --- Stage 1a/1b: safety, always runs first, no exceptions -------------
    with timer.stage("safety_layer1") as s:
        safety_result = safety.check_layer1_rules(pipeline_input.student_question, pipeline_input.conversation_context)
        s.detail["jailbreak_detected"] = safety_result.layer1_jailbreak_detected
        s.detail["academic_integrity_detected"] = safety_result.layer1_academic_integrity_detected
        s.detail["is_safe"] = safety_result.is_safe
    trace.append(
        f"safety_layer1=pass jailbreak={safety_result.layer1_jailbreak_detected} "
        f"academic_integrity={safety_result.layer1_academic_integrity_detected}"
    )
    if not safety_result.is_safe:
        mode = "refused_layer1_academic_integrity" if safety_result.layer1_academic_integrity_detected else "refused_layer1_jailbreak"
        _save_history(
            pipeline_input.uid, grade, "", "", pipeline_input.student_question, pipeline_input.student_question,
            {"format_decision": None, "text_narration": safety_result.refusal_reason},
            llm_action="UNAUTHORIZED", mode=mode,
        )
        return finish(PipelineResult(final_answer=safety_result.refusal_reason, status="REFUSED", safety=safety_result, trace=trace))

    with timer.stage("safety_layer2") as s:
        layer2_result = safety.check_layer2_moderation(pipeline_input.student_question)
        s.detail["categories_flagged"] = layer2_result.layer2_categories_flagged
        s.detail["borderline"] = layer2_result.layer2_borderline
        s.detail["is_safe"] = layer2_result.is_safe
    trace.append(f"safety_layer2 flagged={layer2_result.layer2_categories_flagged} borderline={layer2_result.layer2_borderline}")
    if not layer2_result.is_safe:
        _save_history(
            pipeline_input.uid, grade, "", "", pipeline_input.student_question, pipeline_input.student_question,
            {"format_decision": None, "text_narration": layer2_result.refusal_reason},
            llm_action="UNAUTHORIZED", mode="refused_layer2_moderation",
        )
        return finish(PipelineResult(final_answer=layer2_result.refusal_reason, status="REFUSED", safety=layer2_result, trace=trace))

    # --- Stage 2: reformulate + grounded guess (no curriculum verdict) -----
    with timer.stage("understanding_llm"):
        raw_understanding = understanding.understand_question(
            pipeline_input.student_question, pipeline_input.conversation_context,
            pipeline_input.learner_context, openai_client, model_name,
            llm_calls=llm_calls,
        )
    validation = understanding.to_validation_result(raw_understanding)
    trace.append(f"validation={validation.classification}")

    # Computed unconditionally, regardless of validation outcome - Layer 3
    # below needs curriculum_guess even for an INVALID/UNSUPPORTED-labeled
    # question, per the structural safeguard described next.
    resolved = understanding.to_resolved_question(raw_understanding, pipeline_input.student_question)
    intent = understanding.to_intent_result(raw_understanding)
    curriculum_guess = understanding.to_curriculum_guess(raw_understanding, grade)
    query = understanding.to_reformulated_query(raw_understanding, pipeline_input.student_question, resolved.resolved_question)
    trace.append(f"reformulated subject_guess={curriculum_guess.subject} topic_guess={curriculum_guess.topic}")

    # --- Stage 1c: residual-band recheck, ALWAYS runs first if Layer 2 was
    # borderline - deliberately BEFORE the validation-classification exits
    # below, and deliberately not gated on what Stage 2's own validation
    # says. Structural safeguard, not just a prompt instruction: Stage 2 is
    # a probabilistic LLM call and was found live (2026-08-30) to
    # occasionally mislabel a borderline-safety question as INVALID/
    # UNSUPPORTED itself (e.g. reasoning "not educational") - if that
    # happened to also short-circuit Layer 3, the one mechanism designed to
    # correctly resolve real curriculum content (reproductive biology,
    # historical violence) against a false safety flag would silently never
    # run. This ordering guarantees Layer 3 always gets the final say on
    # anything Layer 2 flagged, regardless of what Stage 2 concluded.
    curriculum_decision = None
    if layer2_result.layer2_borderline:
        with timer.stage("safety_layer3") as s:
            layer3_result, curriculum_decision = safety.residual_recheck(curriculum_guess, grade)
            s.detail["result"] = layer3_result.layer3_result
            s.detail["is_safe"] = layer3_result.is_safe
        trace.append(f"safety_layer3 ran result={layer3_result.layer3_result}")
        if not layer3_result.is_safe:
            _save_history(
                pipeline_input.uid, grade, curriculum_guess.subject, curriculum_guess.chapter,
                pipeline_input.student_question, resolved.resolved_question,
                {"format_decision": None, "text_narration": layer3_result.refusal_reason},
                llm_action="UNAUTHORIZED", mode="refused_layer3_residual",
            )
            return finish(PipelineResult(final_answer=layer3_result.refusal_reason, status="REFUSED", safety=layer3_result, validation=validation, trace=trace))
        # Layer 3 resolved this as genuinely safe (real curriculum content
        # backs it) - fall through past the validation-classification exits
        # below even if Stage 2 had labeled it INVALID/UNSUPPORTED, since
        # Layer 3's real-content verdict is more authoritative than Stage
        # 2's own opinion for exactly this borderline band.
        validation.classification = "VALID"

    if validation.classification in ("INVALID", "UNSUPPORTED"):
        status = "REFUSED" if validation.classification == "UNSUPPORTED" else "CLARIFICATION_NEEDED"
        answer = validation.clarification_prompt or "I can only help with school curriculum topics for your class - could you ask about that instead?"
        _save_history(
            pipeline_input.uid, grade, "", "", pipeline_input.student_question, pipeline_input.student_question,
            {"format_decision": None, "text_narration": answer},
            llm_action=validation.classification, mode="validation_exit",
        )
        return finish(PipelineResult(final_answer=answer, status=status, safety=layer2_result, validation=validation, trace=trace))

    if validation.classification == "AMBIGUOUS":
        answer = validation.clarification_prompt or "Could you give me a bit more detail on what you'd like explained?"
        _save_history(
            pipeline_input.uid, grade, "", "", pipeline_input.student_question, pipeline_input.student_question,
            {"format_decision": None, "text_narration": answer},
            llm_action="AMBIGUOUS", mode="clarification_needed",
        )
        return finish(PipelineResult(
            final_answer=answer,
            status="CLARIFICATION_NEEDED", safety=layer2_result, validation=validation, trace=trace,
        ))

    route_decision = routing.route_question(validation, intent)
    trace.append(f"route={route_decision.route}")
    is_followup = route_decision.route == "FOLLOW_UP"

    # --- Stage 3: shared cache check ----------------------------------------
    with timer.stage("global_cache_check") as s:
        s.detail["cache_key"] = {
            "grade": grade, "subject": curriculum_guess.subject, "board": board,
            "language": language, "chapter": curriculum_guess.chapter,
        }
        cache_hit = _check_cache(query.resolved_question, grade, curriculum_guess.subject, board, language, curriculum_guess.chapter)
        s.detail["result"] = "hit" if cache_hit else "miss"
    trace.append(f"cache={'hit' if cache_hit else 'miss'}")
    if cache_hit:
        out = cache_hit.get("orchestrator_output", {})
        _save_history(
            pipeline_input.uid, grade, curriculum_guess.subject, curriculum_guess.chapter,
            pipeline_input.student_question, query.resolved_question,
            {"format_decision": out.get("format_decision"), "text_narration": out.get("text_narration")},
            llm_action="CURRICULUM" if curriculum_guess.subject else "GENERAL_KNOWLEDGE", mode="cache_hit",
        )
        return finish(PipelineResult(
            final_answer=out.get("text_narration") or "",
            status="ANSWERED", safety=layer2_result, validation=validation, resolved=resolved,
            intent=intent, curriculum_guess=curriculum_guess, query=query, routing=route_decision,
            format_decision=out.get("format_decision"), trace=trace,
        ))

    # --- Stage 4: curriculum branch (real RAG call, reused from Stage 1c) --
    if curriculum_decision is None:
        with timer.stage("curriculum_decision") as s:
            curriculum_decision = rag_stage.decide_curriculum(curriculum_guess, grade, query=query, session_book_uuid=pipeline_input.session_context.get("book_uuid", ""), debug=s.detail)
            s.detail["curriculum_match"] = curriculum_decision.curriculum_match
            s.detail["confidence_tier"] = curriculum_decision.rag.confidence_tier if curriculum_decision.rag else None
    trace.append(f"curriculum_match={curriculum_decision.curriculum_match} tier={curriculum_decision.rag.confidence_tier if curriculum_decision.rag else None} decided_at={curriculum_decision.decided_at_stage}")

    # --- Stage 5: fetch -------------------------------------------------------
    is_curriculum = curriculum_decision.curriculum_match == "in_grade"
    rag_result = None
    if is_curriculum:
        with timer.stage("rag_fetch") as s:
            rag_result = rag_stage.fetch_for_answer(
                curriculum_decision, pipeline_input.session_id, is_followup,
                curriculum_guess=curriculum_guess, debug=s.detail,
            )
            s.detail["retrieval_status"] = rag_result.retrieval_status
            s.detail["chunk_count"] = len(rag_result.sources)
        trace.append(f"fetch status={rag_result.retrieval_status} chunks={len(rag_result.sources)}")

    # --- Stage 6: format decision + generation ---------------------------------
    with timer.stage("answer_generation"):
        if is_curriculum:
            result = generation.generate_answer(query.resolved_question, grade, rag_result.context, is_curriculum=True, openai_client=openai_client, model_name=model_name, llm_calls=llm_calls, learner_context=pipeline_input.learner_context)
        elif routing.should_use_web_search(query.resolved_question):
            trace.append("non_curriculum route=web_search")
            result = generation.generate_web_search_answer(query.resolved_question, grade, board, pipeline_input.conversation_context, openai_client, model_name, llm_calls=llm_calls)
        else:
            trace.append("non_curriculum route=direct_llm")
            result = generation.generate_answer(query.resolved_question, grade, "", is_curriculum=False, openai_client=openai_client, model_name=model_name, llm_calls=llm_calls, learner_context=pipeline_input.learner_context)
        # Shared corrective pass (personalization parity requirement,
        # 2026-09-02): applies RESPONSE_STYLE_PREFERENCE reliably regardless
        # of which branch above produced text_narration, same as the old
        # flow's single restyle gate applied uniformly after its one call.
        result = generation.apply_personalized_restyle(result, pipeline_input.learner_context, pipeline_input.student_question, openai_client, model_name)
    trace.append(f"format_decision={result['format_decision']}")

    # --- Stage 8: save (Stage 7/delivery is the caller's job - TTS/video) ------
    with timer.stage("save"):
        _save_cache(query.resolved_question, grade, curriculum_guess.subject, board, language, result, curriculum_guess.chapter)
        _save_history(
            pipeline_input.uid, grade, curriculum_guess.subject, curriculum_guess.chapter,
            pipeline_input.student_question, query.resolved_question, result,
            llm_action="CURRICULUM" if is_curriculum else "GENERAL_KNOWLEDGE", mode="fresh",
        )
    trace.append("saved")

    return finish(PipelineResult(
        final_answer=result.get("text_narration") or "",
        status="ANSWERED",
        safety=layer2_result, validation=validation, resolved=resolved, intent=intent,
        curriculum_guess=curriculum_guess, curriculum_decision=curriculum_decision, rag_result=rag_result, query=query,
        routing=route_decision, format_decision=result.get("format_decision"), trace=trace,
    ))


def _check_cache(standalone_question: str, grade: int, subject: str, board: str, language: str, chapter: str = "") -> Optional[Dict]:
    from backend.app.core.firestore_service import check_global_query_cache
    try:
        return check_global_query_cache(standalone_question, grade, subject, board, language, chapter)
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Cache] check failed, treating as miss: {e}")
        return None


def _save_history(
    uid: str, grade: int, subject: str, chapter: str,
    raw_question: str, resolved_question: str, result: Dict,
    llm_action: str, mode: str = "fresh",
) -> None:
    """
    Stage 8's OTHER write - the student's own personal History
    (users/{uid}/queries), distinct from the shared cache. Per the
    project's own cross-cutting constraint, this NEVER changes the existing
    analytics_service.log_query()/update_user_stats() contract - only calls
    it with real values - and is fail-open: a failure here must never break
    the student-facing answer, same as every other personalization write in
    this codebase.

    Called at EVERY exit point of the pipeline, not just successful answers
    (2026-08-30, explicit product decision) - a refused/unsafe question is
    exactly the kind of thing a future parent/admin dashboard needs to see,
    not something that should silently vanish. llm_action distinguishes the
    outcome (CURRICULUM/GENERAL_KNOWLEDGE for real answers, UNAUTHORIZED for
    a safety block, INVALID/UNSUPPORTED/AMBIGUOUS for a validation exit) -
    an existing field on log_query(), not a new one.
    """
    if not uid:
        logger.warning("[QUESTION_PIPELINE][History] No uid provided - skipping personal history write.")
        return
    try:
        from backend.app.services.analytics import analytics_service
        analytics_service.log_query(
            uid=uid,
            class_name=str(grade),
            subject=subject or "general knowledge",
            chapter_id=None,
            chapter_name=chapter or None,
            query=raw_question,
            reformulated_query=resolved_question,
            mode=mode,
            llm_action=llm_action,
            answer_length=len(result.get("text_narration") or ""),
            format_decision=result.get("format_decision"),
            llm_response=result.get("text_narration"),
        )
        analytics_service.update_user_stats(uid=uid, subject=subject or "general knowledge", chapter_id=None, class_name=str(grade))
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][History] Failed to log personal history for uid={uid}: {e}")


def _save_cache(standalone_question: str, grade: int, subject: str, board: str, language: str, result: Dict, chapter: str = "") -> None:
    from backend.app.core.firestore_service import save_to_global_query_cache
    if not result.get("text_narration"):
        return  # nothing to cache for a VIDEO_REQUIRED turn here - video pipeline saves its own record
    orchestrator_output = {
        "matched_subject": subject,
        "format_decision": result.get("format_decision"),
        "text_narration": result.get("text_narration"),
    }
    try:
        save_to_global_query_cache(standalone_question, grade, subject, orchestrator_output, board=board, language=language, chapter=chapter)
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Cache] save failed: {e}")
