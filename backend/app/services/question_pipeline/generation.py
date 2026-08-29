"""
Stages 11-12 (spec sections 11, 13, 14): build the structured LLM prompt and
generate the answer. The LLM's only job here is "use the validated RAG
context and learner context to explain the answer appropriately" (section
14) - it is NOT asked to find chunks, pick a database, or invent curriculum
mappings; those already happened in earlier stages.
"""
import logging

from backend.app.services.question_pipeline.schemas import (
    CurriculumContext,
    IntentResult,
    RAGResult,
    ReformulatedQuery,
)

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """SYSTEM INSTRUCTIONS
You are a patient K-12 tutor. Answer using ONLY the retrieved context below plus ordinary
pedagogical explanation of it - do not introduce facts that aren't supported by the context.
If the context doesn't actually cover the question, say so plainly instead of guessing.

STUDENT QUESTION
{student_question}

RESOLVED QUESTION
{resolved_question}

INTENT
{intent}

RELEVANT LEARNER CONTEXT
{learner_context}

CURRICULUM CONTEXT
Class: {class_name} | Subject: {subject} | Chapter: {chapter} | Topic: {topic} | Concept: {concept}

RETRIEVED CONTEXT
{retrieved_context}

RESPONSE REQUIREMENTS
{response_requirements}
"""

_INTENT_RESPONSE_REQUIREMENTS = {
    "DEFINE": "Give a precise, class-appropriate definition, then one short example.",
    "EXPLAIN": "Explain simply, step by step, with one relevant example.",
    "WHY": "Explain the cause/reason clearly, in class-appropriate language.",
    "HOW": "Explain the process or mechanism step by step.",
    "SOLVE": "Show the full step-by-step solution, not just the final answer.",
    "CALCULATE": "Show the formula, substitution, and final numeric answer with units.",
    "COMPARE": "Present a clear point-by-point comparison.",
    "SUMMARIZE": "Give a concise summary covering the key points only.",
    "EXAMPLE": "Give one or two concrete, relatable examples.",
    "PRACTICE": "Offer a short practice question related to the topic.",
    "ASSESS": "Ask an assessment-style question to check understanding.",
    "REVISE": "Give a quick, structured recap of the key points.",
    "FOLLOW_UP": "Answer the resolved follow-up question directly and concisely.",
    "CLARIFY": "Ask a short clarifying question before answering.",
}


def build_prompt(
    original_question: str,
    query: ReformulatedQuery,
    intent: IntentResult,
    curriculum: CurriculumContext,
    rag: RAGResult,
    learner_context: dict,
) -> str:
    learner_summary = ", ".join(f"{k}: {v}" for k, v in learner_context.items() if v) or "(none provided)"
    requirement = _INTENT_RESPONSE_REQUIREMENTS.get(intent.primary_intent, "Explain clearly and simply.")

    return _PROMPT_TEMPLATE.format(
        student_question=original_question,
        resolved_question=query.resolved_question,
        intent=intent.primary_intent,
        learner_context=learner_summary,
        class_name=curriculum.class_name or "unknown",
        subject=curriculum.subject or "unknown",
        chapter=curriculum.chapter or "unknown",
        topic=curriculum.topic or "unknown",
        concept=curriculum.concept or "unknown",
        retrieved_context=rag.context or "(no context retrieved)",
        response_requirements=requirement,
    )


def generate_answer(prompt: str, openai_client, model_name: str) -> str:
    try:
        response = openai_client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={"temperature": 0.4},
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Generation] LLM call failed: {e}")
        return "Sorry, I couldn't generate an answer right now. Please try again."
