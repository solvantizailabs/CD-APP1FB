"""
Stages 1-6 of the spec (sections 4-8): Question Validation, Follow-up/Context
Resolution, Intent Classification, Curriculum Context Identification, and
Query Reformulation.

These five sub-tasks all read the same two inputs (the student's question and
recent conversation context) and produce structured fields about that single
question - none of them touch RAG, curriculum documents, or answer
generation. Bundling them into one structured-output LLM call is a "query
understanding" call, not the "one giant LLM prompt" anti-pattern the spec
warns against in section 16 (that anti-pattern is the CURRENT system: one
call that also does retrieval-adjacent subject/chapter matching AND answer
generation together). Each sub-task still gets its own typed dataclass
output (schemas.py) so it stays independently inspectable/testable per the
per-stage checklist in section 17.

Intents and validation classes are controlled enums (schemas.py) - the LLM
is constrained to pick from the given list, never to invent new labels
(section 5, section 16).
"""
import json
import logging
from typing import Dict, List, Optional

from backend.app.services.question_pipeline.schemas import (
    CurriculumContext,
    INTENTS,
    IntentResult,
    ReformulatedQuery,
    ResolvedQuestion,
    VALIDATION_CLASSES,
    ValidationResult,
)

logger = logging.getLogger(__name__)

_UNDERSTANDING_PROMPT = """You are the question-understanding stage of a K-12 tutoring system.
Analyze the student's question using ONLY the recent conversation turns given below for context.
Do not answer the question. Do not invent curriculum facts you are not given evidence for.

Recent conversation (most recent last, may be empty):
{conversation_summary}

Known learner context (may be partial or empty - use to fill gaps, never override the question itself):
{learner_context_summary}

Student's current question:
"{student_question}"

Return ONLY a single JSON object with this exact shape:
{{
  "validation_classification": one of {validation_classes},
  "validation_reason": short string,
  "clarification_prompt": string or null (only set if validation_classification is AMBIGUOUS or INVALID),
  "is_follow_up": true or false,
  "resolved_question": the student's question rewritten as a fully self-contained question
      (resolve pronouns like "this"/"it"/"that" and elided subjects using the conversation above;
      if not a follow-up, this is just the original question, lightly cleaned up),
  "resolution_reason": short string explaining what was resolved, or "not a follow-up",
  "primary_intent": one of {intents},
  "secondary_intent": one of {intents} or null,
  "intent_confidence": number between 0 and 1,
  "class_name": string or "" (grade/class, e.g. "8"),
  "subject": string or "",
  "chapter": string or "",
  "topic": string or "",
  "concept": string or "",
  "curriculum_confidence": number between 0 and 1 (0 if you are guessing with low confidence - do not guess wildly),
  "semantic_query": a clean, well-formed natural-language query suitable for semantic search over a textbook,
  "keyword_query": a short keyword-style query (key terms only) for lexical/BM25-style search
}}

Rules:
- validation_classification must be exactly one of: VALID, FOLLOW_UP, AMBIGUOUS, INVALID, UNSUPPORTED.
  - INVALID: gibberish, empty, or not a real question.
  - UNSUPPORTED: valid question but clearly out of syllabus scope or requests something this system cannot do (e.g. "hack a website").
  - AMBIGUOUS: understandable but missing information needed to answer (e.g. "explain it" with no prior context).
  - FOLLOW_UP: depends on the immediately preceding conversation to be understood.
  - VALID: a clear, self-contained, in-scope question.
- primary_intent and secondary_intent must come only from the given intent list. Use null for secondary_intent if there isn't one.
- Only fill class_name/subject/chapter/topic/concept when you have real evidence from the question or learner context. Leave blank rather than guessing, and reflect that in curriculum_confidence.
- Output raw JSON only, no markdown fences, no commentary.
"""


def _format_conversation(conversation_context: List[Dict]) -> str:
    if not conversation_context:
        return "(no prior turns)"
    lines = []
    for turn in conversation_context[-3:]:
        q = turn.get("query") or turn.get("question") or ""
        a = (turn.get("answer") or "")[:200]
        lines.append(f"Q: {q}\nA: {a}")
    return "\n\n".join(lines)


def _format_learner_context(learner_context: Dict) -> str:
    if not learner_context:
        return "(none provided)"
    return json.dumps({k: v for k, v in learner_context.items() if v}, ensure_ascii=False)


def _extract_json(text: str) -> Dict:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in understanding-stage LLM response")
    return json.loads(text[start:end])


def understand_question(
    student_question: str,
    conversation_context: List[Dict],
    learner_context: Dict,
    openai_client,
    model_name: str,
) -> Dict:
    """
    Runs the single structured "understanding" LLM call and returns the raw
    parsed dict. pipeline.py splits this into the four typed dataclasses
    (ValidationResult, ResolvedQuestion, IntentResult, CurriculumContext,
    ReformulatedQuery) so downstream code never touches this raw shape.
    """
    prompt = _UNDERSTANDING_PROMPT.format(
        conversation_summary=_format_conversation(conversation_context),
        learner_context_summary=_format_learner_context(learner_context),
        student_question=student_question,
        validation_classes=list(VALIDATION_CLASSES),
        intents=list(INTENTS),
    )

    try:
        response = openai_client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={"temperature": 0.1, "response_mime_type": "application/json"},
        )
        return _extract_json(response.text)
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Understanding] LLM call failed: {e}")
        # Fail safe: treat as INVALID rather than silently guessing curriculum context.
        return {
            "validation_classification": "INVALID",
            "validation_reason": f"understanding stage error: {e}",
            "clarification_prompt": "Sorry, could you rephrase your question?",
            "is_follow_up": False,
            "resolved_question": student_question,
            "resolution_reason": "understanding stage error",
            "primary_intent": "CLARIFY",
            "secondary_intent": None,
            "intent_confidence": 0.0,
            "class_name": "",
            "subject": "",
            "chapter": "",
            "topic": "",
            "concept": "",
            "curriculum_confidence": 0.0,
            "semantic_query": student_question,
            "keyword_query": student_question,
        }


def to_validation_result(raw: Dict) -> ValidationResult:
    classification = raw.get("validation_classification", "INVALID")
    if classification not in VALIDATION_CLASSES:
        classification = "INVALID"
    return ValidationResult(
        classification=classification,
        reason=raw.get("validation_reason", ""),
        clarification_prompt=raw.get("clarification_prompt"),
    )


def to_resolved_question(raw: Dict, original_question: str) -> ResolvedQuestion:
    return ResolvedQuestion(
        original_question=original_question,
        resolved_question=raw.get("resolved_question") or original_question,
        used_follow_up=bool(raw.get("is_follow_up")),
        resolution_reason=raw.get("resolution_reason", ""),
    )


def to_intent_result(raw: Dict) -> IntentResult:
    primary = raw.get("primary_intent", "CLARIFY")
    if primary not in INTENTS:
        primary = "CLARIFY"
    secondary = raw.get("secondary_intent")
    if secondary not in INTENTS:
        secondary = None
    try:
        confidence = float(raw.get("intent_confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return IntentResult(primary_intent=primary, secondary_intent=secondary, confidence=confidence)


def to_curriculum_context(raw: Dict) -> CurriculumContext:
    try:
        confidence = float(raw.get("curriculum_confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return CurriculumContext(
        class_name=raw.get("class_name") or "",
        subject=raw.get("subject") or "",
        chapter=raw.get("chapter") or "",
        topic=raw.get("topic") or "",
        concept=raw.get("concept") or "",
        confidence=confidence,
    )


def to_reformulated_query(raw: Dict, original_question: str, resolved_question: str) -> ReformulatedQuery:
    curriculum_filters = {
        k: raw.get(k) for k in ("class_name", "subject", "chapter", "topic", "concept") if raw.get(k)
    }
    return ReformulatedQuery(
        original_question=original_question,
        resolved_question=resolved_question,
        semantic_query=raw.get("semantic_query") or resolved_question,
        keyword_query=raw.get("keyword_query") or resolved_question,
        metadata_filters=curriculum_filters,
    )
