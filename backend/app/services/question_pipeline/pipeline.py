"""
Section 18 "Complete Implementation Flow" / section 20 fault-isolation:

Student Question -> Question Validation -> Context/Follow-up Resolution ->
Intent Detection -> Curriculum Context ID -> Query Reformulation ->
Query Routing -> Existing RAG API -> Context Validation -> LLM Prompt Build
-> LLM Generation -> Grounding Validation -> Final Answer.

This is the standalone entry point (`run_pipeline`). It is deliberately NOT
wired into backend/app/api/routes/chat.py yet - per the doc's section 1
("we should not modify the completed RAG logic unless testing identifies an
actual retrieval issue") and to keep the live /api/smart_query path stable
while this new layer is validated against the section 17 test cases first
(see question_pipeline/test_harness.py).
"""
import logging
from typing import Optional

from backend.app.services.question_pipeline import (
    context_validation,
    generation,
    grounding,
    rag_stage,
    routing,
    understanding,
)
from backend.app.services.question_pipeline.schemas import PipelineInput, PipelineResult

logger = logging.getLogger(__name__)


def run_pipeline(
    pipeline_input: PipelineInput,
    openai_client,
    model_name: str,
    has_image: bool = False,
) -> PipelineResult:
    trace = []

    # 1-6: Question Understanding (validation, follow-up resolution, intent, curriculum, reformulation)
    raw_understanding = understanding.understand_question(
        pipeline_input.student_question,
        pipeline_input.conversation_context,
        pipeline_input.learner_context,
        openai_client,
        model_name,
    )
    validation = understanding.to_validation_result(raw_understanding)
    trace.append(f"validation={validation.classification}")

    resolved = understanding.to_resolved_question(raw_understanding, pipeline_input.student_question)
    trace.append(f"resolved_question={'yes' if resolved.used_follow_up else 'no-op'}")

    intent = understanding.to_intent_result(raw_understanding)
    trace.append(f"intent={intent.primary_intent}")

    curriculum = understanding.to_curriculum_context(raw_understanding)
    trace.append(f"curriculum=class:{curriculum.class_name}/subject:{curriculum.subject}")

    # Section 6: prefer learner-profile values to fill gaps, never override the question's own signal.
    if not curriculum.class_name and pipeline_input.learner_context.get("class_name"):
        curriculum.class_name = pipeline_input.learner_context["class_name"]
    if not curriculum.subject and pipeline_input.session_context.get("subject"):
        curriculum.subject = pipeline_input.session_context["subject"]

    query = understanding.to_reformulated_query(raw_understanding, pipeline_input.student_question, resolved.resolved_question)

    # 7: Query Routing (deterministic)
    route_decision = routing.route_question(validation, intent, has_image=has_image)
    trace.append(f"route={route_decision.route}")

    if route_decision.route in ("AMBIGUOUS", "UNSUPPORTED"):
        status = "CLARIFICATION_NEEDED" if route_decision.route == "AMBIGUOUS" else "REFUSED"
        answer = validation.clarification_prompt or (
            "I can only help with school curriculum topics for your class - could you ask about that instead?"
        )
        return PipelineResult(
            final_answer=answer,
            status=status,
            validation=validation,
            resolved=resolved,
            intent=intent,
            curriculum=curriculum,
            query=query,
            routing=route_decision,
            trace=trace,
        )

    if route_decision.route == "IMAGE":
        return PipelineResult(
            final_answer="Image/document questions are routed to vision/OCR extraction, which is out of scope for this pipeline stage.",
            status="REFUSED",
            validation=validation,
            resolved=resolved,
            intent=intent,
            curriculum=curriculum,
            query=query,
            routing=route_decision,
            trace=trace,
        )

    # 8: Existing RAG API (unmodified)
    curriculum = rag_stage.resolve_book_and_chapter(curriculum, pipeline_input.session_context.get("book_uuid", ""))
    rag_result = rag_stage.call_rag(query, curriculum)
    trace.append(f"rag_status={rag_result.retrieval_status}, tier={rag_result.confidence_tier}")

    # 9: Context Validation
    ctx_validation = context_validation.validate_context(rag_result)
    trace.append(f"context_sufficient={ctx_validation.is_sufficient}")

    if not ctx_validation.is_sufficient:
        return PipelineResult(
            final_answer="I don't have enough verified textbook content to answer that accurately yet - could you check the topic name or ask something else from this chapter?",
            status="INSUFFICIENT_CONTEXT",
            validation=validation,
            resolved=resolved,
            intent=intent,
            curriculum=curriculum,
            query=query,
            routing=route_decision,
            rag=rag_result,
            context_validation=ctx_validation,
            trace=trace,
        )

    # 10-11: LLM Prompt Build + Generation
    prompt = generation.build_prompt(
        pipeline_input.student_question, query, intent, curriculum, rag_result, pipeline_input.learner_context
    )
    answer = generation.generate_answer(prompt, openai_client, model_name)
    trace.append("answer_generated")

    # 12: Grounding Validation
    grounding_result = grounding.validate_grounding(answer, rag_result.context, curriculum)
    trace.append(f"grounded={grounding_result.is_grounded}")

    if not grounding_result.is_grounded:
        # Section 12/15: one retry with an explicit "stick to context" instruction, no silent hallucination.
        retry_prompt = prompt + "\n\nIMPORTANT: Your previous attempt included claims not supported by the retrieved context. Rewrite the answer using ONLY the retrieved context above."
        answer = generation.generate_answer(retry_prompt, openai_client, model_name)
        grounding_result = grounding.validate_grounding(answer, rag_result.context, curriculum)
        trace.append(f"regenerated_grounded={grounding_result.is_grounded}")

        if not grounding_result.is_grounded:
            answer = "I want to make sure I give you an accurate answer, but I couldn't fully verify it against the textbook content. Could you rephrase the question or ask about a related topic?"
            return PipelineResult(
                final_answer=answer,
                status="INSUFFICIENT_CONTEXT",
                validation=validation,
                resolved=resolved,
                intent=intent,
                curriculum=curriculum,
                query=query,
                routing=route_decision,
                rag=rag_result,
                context_validation=ctx_validation,
                grounding=grounding_result,
                trace=trace,
            )

    return PipelineResult(
        final_answer=answer,
        status="ANSWERED",
        validation=validation,
        resolved=resolved,
        intent=intent,
        curriculum=curriculum,
        query=query,
        routing=route_decision,
        rag=rag_result,
        context_validation=ctx_validation,
        grounding=grounding_result,
        trace=trace,
    )
