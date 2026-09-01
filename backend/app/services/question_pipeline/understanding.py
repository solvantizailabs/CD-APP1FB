"""
Stage 2 of the FRD v3 flow: Question Validation, Follow-up/Context
Resolution, Intent Classification, and a GROUNDED (not authoritative)
curriculum guess + reformulation.

Reuses the master_orchestrator_prompt.txt's existing Section 3 (context
ingestion, curriculum data injection, IMMEDIATE_PRIOR_TURN resolution) and
Section 4 (reformulation + Rules 1B/1C/1D: synonym matching, comparison-
question handling, follow-up inheritance) CONTENT, not a rewrite from
scratch - only the safety directives, the `classification`/`is_authorized`
output fields, and anything about format/generation are stripped, since
those now live in other stages (safety.py, rag_stage.py, generation.py).

Critical distinction (see docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md,
"Correction 2026-08-30" under Stage 2): this call DOES see real curriculum
data (get_cached_curriculum_metadata), same as the live system today - that
is what makes its subject/topic guess grounded instead of a blind
vocabulary guess. What it does NOT do is decide curriculum-or-not; that
verdict belongs entirely to Stage 4's real RAG call
(rag_stage.decide_curriculum), never to this call's opinion.
"""
import json
import logging
import time
from typing import Dict, List, Optional

from backend.app.services.question_pipeline.observability.llm_call import call_llm
from backend.app.services.question_pipeline.schemas import (
    CurriculumGuess,
    INTENTS,
    IntentResult,
    ReformulatedQuery,
    ResolvedQuestion,
    VALIDATION_CLASSES,
    ValidationResult,
)

logger = logging.getLogger(__name__)

_UNDERSTANDING_PROMPT = """You are the question-understanding stage of a K-12 tutoring system for Indian
school students (NCERT curriculum). Analyze the student's question using ONLY the
recent conversation turns and curriculum data given below. Do not answer the
question. Do not invent curriculum facts you are not given evidence for.

STUDENT_GRADE: Class {student_grade}

CURRICULUM_DATA (real chapter titles + summaries actually available for this grade -
use this to GROUND your subject/topic guess, matching synonyms and differently-phrased
concepts, not just exact words; e.g. "digestive system" = "alimentary canal",
"respiratory system" = "breathing". A comparison question ("compare X and Y") counts
as a match if BOTH concepts appear together in one chapter's summary below - do not
let trivia-style phrasing push you away from a real match):
{curriculum_data}

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
  "resolved_question": the student's question rewritten as a fully self-contained question
      (resolve pronouns like "this"/"it"/"that" and elided subjects using the conversation above -
      a pronoun/vague reference means the MOST RECENT TURN labeled above, never an earlier turn in
      the list, even if an earlier turn seems like a plausible fit - recency wins unless the current
      question explicitly names a different, earlier topic itself;
      if the referent is itself a follow-up on a topic, inherit that topic's subject/chapter rather
      than treating the follow-up's own empty-of-keywords text as unrelated to it;
      if not a follow-up, this is just the original question, lightly cleaned up),
  "resolution_reason": short string explaining what was resolved, or "not a follow-up",
  "primary_intent": one of {intents},
  "secondary_intent": one of {intents} or null,
  "intent_confidence": number between 0 and 1,
  "subject": string or "" (ONLY set this if it genuinely matches something in CURRICULUM_DATA above -
      this is a guess to search against, not a final verdict, but it should be grounded, not invented),
  "chapter": string or "",
  "topic": string or "",
  "concept": string or "",
  "guess_confidence": number between 0 and 1 (0 if you have no real curriculum match - do not guess wildly),
  "semantic_query": a clean, well-formed natural-language query suitable for semantic search over a textbook,
  "keyword_query": a short keyword-style query (key terms only) for lexical/BM25-style search
}}

Rules:
- validation_classification must be exactly one of: VALID, FOLLOW_UP, AMBIGUOUS, INVALID, UNSUPPORTED.
  - INVALID: gibberish, empty, or not a real question AT ALL (e.g. random characters, a single word with no
    question intent). A well-formed, real question is NEVER INVALID just because it doesn't match anything
    in CURRICULUM_DATA above - whether the topic is actually covered by this student's textbook is NOT this
    stage's decision (a separate, later step verifies that against real retrieved content, not this guess).
    A real, understandable question with no curriculum match should be VALID, with subject/chapter/topic
    left blank - do not mark it INVALID or UNSUPPORTED for that reason.
  - CRITICAL - a topic change from the recent conversation above is NORMAL, not invalid. If the recent
    conversation was about one topic (e.g. light/refraction) and the CURRENT question is about something
    completely different (e.g. sports, history, a different subject) - that is simply a NEW question on a
    NEW topic. Never classify a question as INVALID or reason "unrelated to the curriculum/conversation" -
    a real, well-formed question is VALID (or FOLLOW_UP only if it actually depends on resolving a pronoun/
    reference from the prior turn) regardless of how different it is from what was just discussed.
  - CRITICAL - validity has NOTHING to do with whether a topic is sensitive, appropriate, or "educational."
    Do NOT classify as INVALID or UNSUPPORTED because a question sounds disturbing, unethical, or off-topic
    for a classroom (e.g. a request for graphic violent content, or a request involving academic dishonesty).
    That judgment belongs entirely to a separate safety layer that runs independently of this stage - your
    ONLY job here is: is this real, understandable text forming an actual question? If yes, it is VALID (or
    FOLLOW_UP/AMBIGUOUS per the normal rules) no matter how inappropriate or unsafe its subject matter is.
    Marking a real question INVALID/UNSUPPORTED for this reason is a bug, not a safety feature - it silently
    breaks the pipeline's actual safety mechanism, which depends on this stage staying purely about validity.
  - UNSUPPORTED: reserve this ONLY for a real, clear question asking for something this system's FORMAT
    literally cannot deliver, regardless of topic (e.g. "get on a live video call with me", "send an email
    for me", "book something for me"). This is about functional capability (text/audio Q&A only), never
    about whether the topic itself is appropriate - do not use UNSUPPORTED as a soft way to refuse a
    sensitive topic; that is not what this field is for.
  - AMBIGUOUS: understandable but missing information needed to answer, AND there is no usable prior turn
    to resolve it against (e.g. "explain it" as the very first message, recent conversation empty). If a
    prior turn DOES exist, prefer FOLLOW_UP over AMBIGUOUS - see below.
  - FOLLOW_UP: depends on the immediately preceding conversation to be understood. This is NOT limited to
    an explicit pronoun/reference ("explain it", "why does that happen") - it ALSO covers an implicit
    continuation/elaboration request that names no pronoun at all but clearly builds on the answer just
    given, e.g. "explain in detail", "give me a real-life example", "explain with an example", "go deeper
    into that", "can you simplify it", "what about diagrams". Found live (2026-08-31): "explain with an
    detailed example" right after a real answer was wrongly marked AMBIGUOUS instead of FOLLOW_UP purely
    because it has no pronoun - a request phrased as a bare elaboration/example/detail ask, with a real
    prior turn available, is a FOLLOW_UP on that turn's topic, never AMBIGUOUS, even without "it"/"this"/
    "that". Only classify this style of phrasing as AMBIGUOUS when recent conversation is genuinely empty.
  - VALID: a clear, self-contained, real question - regardless of whether it happens to match CURRICULUM_DATA,
    regardless of topic, and regardless of how sensitive or inappropriate its subject matter is.
- primary_intent and secondary_intent must come only from the given intent list. Use null for secondary_intent if there isn't one.
- subject/chapter/topic/concept: only fill when grounded in CURRICULUM_DATA above. Leave blank rather
  than guessing, and reflect that in guess_confidence. This is NOT the final word on whether the
  question is curriculum - a downstream retrieval step decides that from real content, not from this guess.
- Output raw JSON only, no markdown fences, no commentary.
"""


def _format_conversation(conversation_context: List[Dict]) -> str:
    """
    Found live (2026-08-30): a follow-up ("give me a real-world example of
    that") incorrectly resolved to a topic from 2 turns back instead of the
    immediately preceding turn, when 3 turns were shown with no explicit
    recency signal. Explicitly labeling the most recent turn (and ordering
    oldest-to-newest, ending on it) fixes the ambiguity - a pronoun/vague
    reference overwhelmingly means "the turn right before this one", not
    something further back, and the prompt now says so directly.
    """
    if not conversation_context:
        return "(no prior turns)"
    recent = conversation_context[-3:]
    lines = []
    for i, turn in enumerate(recent):
        q = turn.get("query") or turn.get("question") or ""
        a = (turn.get("answer") or "")[:200]
        label = "MOST RECENT TURN (resolve pronouns/references against THIS one)" if i == len(recent) - 1 else "earlier turn"
        lines.append(f"[{label}]\nQ: {q}\nA: {a}")
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


def _get_curriculum_data(grade: int) -> Dict:
    """Returns {"text", "load_ms", "source"} - timed separately from the LLM
    call itself so the log report can show the curriculum-metadata cache
    load as its own line, distinct from the reformulation LLM's duration."""
    from backend.app.orchestrator_test.test_runner import get_cached_curriculum_metadata
    started = time.time()
    try:
        text = get_cached_curriculum_metadata(grade) or "(no curriculum data available for this grade)"
        return {"text": text, "load_ms": round((time.time() - started) * 1000), "source": "cache"}
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Understanding] curriculum data lookup failed: {e}")
        return {"text": "(curriculum data lookup failed)", "load_ms": round((time.time() - started) * 1000), "source": "error"}


def understand_question(
    student_question: str,
    conversation_context: List[Dict],
    learner_context: Dict,
    openai_client,
    model_name: str,
    llm_calls: Optional[List] = None,
) -> Dict:
    """
    Runs the single structured "understanding" LLM call and returns the raw
    parsed dict. pipeline.py splits this into the typed dataclasses
    (ValidationResult, ResolvedQuestion, IntentResult, CurriculumGuess,
    ReformulatedQuery) so downstream code never touches this raw shape.

    llm_calls: optional list this call appends its LLMCallResult to (see
    observability/llm_call.py) - carries real token/cost/duration plus the
    exact conversation slice sent, for the pipeline log report. Left as
    None by callers (e.g. test_harness.py) that don't need logging.
    """
    grade = learner_context.get("class_name") or learner_context.get("class") or 7
    try:
        grade_int = int("".join(ch for ch in str(grade) if ch.isdigit()) or "0") or 7
    except ValueError:
        grade_int = 7

    curriculum_data = _get_curriculum_data(grade_int)
    conversation_summary = _format_conversation(conversation_context)
    prompt = _UNDERSTANDING_PROMPT.format(
        student_grade=grade_int,
        curriculum_data=curriculum_data["text"],
        conversation_summary=conversation_summary,
        learner_context_summary=_format_learner_context(learner_context),
        student_question=student_question,
        validation_classes=list(VALIDATION_CLASSES),
        intents=list(INTENTS),
    )

    result = call_llm(
        openai_client, model_name, prompt, stage="understanding",
        config={"temperature": 0.1, "response_mime_type": "application/json"},
        extra={
            "grade": grade_int,
            "conversation_summary_sent": conversation_summary,
            "conversation_turns_sent": min(len(conversation_context), 3),
            "curriculum_data_sent": curriculum_data["text"],
            "curriculum_cache_load_ms": curriculum_data["load_ms"],
            "curriculum_cache_source": curriculum_data["source"],
        },
    )
    if llm_calls is not None:
        llm_calls.append(result)

    if result.error:
        logger.warning(f"[QUESTION_PIPELINE][Understanding] LLM call failed: {result.error}")
        return _fail_safe_understanding(student_question, f"understanding stage error: {result.error}")

    try:
        return _extract_json(result.text)
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Understanding] failed to parse LLM response: {e}")
        return _fail_safe_understanding(student_question, f"understanding stage parse error: {e}")


def _fail_safe_understanding(student_question: str, reason: str) -> Dict:
    # Fail safe: treat as INVALID rather than silently guessing curriculum context.
    return {
        "validation_classification": "INVALID",
        "validation_reason": reason,
        "clarification_prompt": "Sorry, could you rephrase your question?",
        "resolved_question": student_question,
        "resolution_reason": "understanding stage error",
        "primary_intent": "CLARIFY",
        "secondary_intent": None,
        "intent_confidence": 0.0,
        "subject": "",
        "chapter": "",
        "topic": "",
        "concept": "",
        "guess_confidence": 0.0,
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
    """
    used_follow_up is derived from validation_classification alone - NOT a
    separate `is_follow_up` field. There used to be one, and it caused a
    real bug found live (2026-08-31, 50-question batch test): the LLM's
    two fields could disagree (e.g. classification="VALID" but
    is_follow_up=true), and since routing.py's route_question() only ever
    read classification, the two disagreeing 6% of the time meant the log
    reported "follow-up" while the pipeline actually ran the NORMAL
    (non-follow-up) path - a silent inconsistency, not just a display bug.
    Removing the redundant field (see the prompt above) makes this the
    single source of truth end-to-end, matching what routing.py already
    uses.
    """
    return ResolvedQuestion(
        original_question=original_question,
        resolved_question=raw.get("resolved_question") or original_question,
        used_follow_up=(raw.get("validation_classification") == "FOLLOW_UP"),
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


def to_curriculum_guess(raw: Dict, grade: int) -> CurriculumGuess:
    try:
        confidence = float(raw.get("guess_confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return CurriculumGuess(
        class_name=str(grade),
        subject=raw.get("subject") or "",
        chapter=raw.get("chapter") or "",
        topic=raw.get("topic") or "",
        concept=raw.get("concept") or "",
        guess_confidence=confidence,
    )


def to_reformulated_query(raw: Dict, original_question: str, resolved_question: str) -> ReformulatedQuery:
    curriculum_filters = {
        k: raw.get(k) for k in ("subject", "chapter", "topic", "concept") if raw.get(k)
    }
    return ReformulatedQuery(
        original_question=original_question,
        resolved_question=resolved_question,
        semantic_query=raw.get("semantic_query") or resolved_question,
        keyword_query=raw.get("keyword_query") or resolved_question,
        metadata_filters=curriculum_filters,
    )
