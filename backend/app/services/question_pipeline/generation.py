"""
Stage 6 (format decision + text generation) of the FRD v3 flow.

Per explicit instruction: this is NOT a rewritten prompt. The persona
(Section 1), format-selection rules (Section 5), and grounding/output rules
(Section 6/7) are reused verbatim from master_orchestrator_prompt.txt -
"the best answer generation prompt" - only its calling context changes: it
used to be one section inside a 415-line all-in-one call that also did
safety/reformulation/classification; here it's a scoped call that runs
AFTER Stage 4/5 have already resolved curriculum content, never before
(this is what structurally eliminates the "answer generated before RAG"
bug - see docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md).

Video storyboard generation is NOT part of this module - confirmed
(visual_learning_service.py) to already be a separate, dedicated pipeline
with its own prompt (visual_lesson_prompt.py), untouched by this redesign.
When this stage returns format_decision="VIDEO_REQUIRED", text_narration is
null and the caller (pipeline.py) hands off to that existing pipeline, same
as the live system already does today.
"""
import datetime
import json
import logging
import re
from typing import Dict, List, Optional

from backend.app.services.question_pipeline.observability.llm_call import call_llm

logger = logging.getLogger(__name__)

# --- direct-answer path (curriculum or non-curriculum, no live web search) --------

_PERSONA = """You are "Chaduvu Guru", a dedicated AI Study Partner for Indian school students
(Classes 6-10, NCERT curriculum: CBSE, ICSE, Indian State Boards). Your tone is warm,
encouraging, respectful, and clear - like a friendly Indian teacher. Use relatable Indian
examples where helpful. CRITICAL VOCABULARY RULE: use extremely simple, age-appropriate
vocabulary (avoid jargon, GRE words, long compound sentences) - explain as if talking to a
10-12 year old. Break explanations into simple step-by-step points."""

_FORMAT_RULES = """FORMAT SELECTION - set format_decision:
"QUICK_ANSWER" if the question asks for: direct facts/names/dates, a single formula or
constant (only when the student wants the value itself, not how to derive/apply it), a
short 1-3 sentence definition, a simple "what is X" question not requiring a multi-step
visual breakdown.

"VIDEO_REQUIRED" if the question asks for: multi-step scientific/mathematical processes
or mechanisms, conceptual topics needing visual diagrams/illustrations/comparative models,
"how does X work" / "why does Y happen" questions requiring deeper explanation, historical
event timelines or governance structures, explicit step-by-step/procedural language ("step
by step", "how do I calculate/derive/solve", "walk me through").
If VIDEO_REQUIRED: set text_narration to null - a separate downstream pipeline builds the
actual video content from the question, do not spend effort writing narration here."""

_GROUNDING_RULES_CURRICULUM = """GROUNDING: ground every fact, definition, formula, and explanation
strictly within the TEXTBOOK CONTEXT provided below. If the context does not actually cover
the question, say so plainly rather than guessing from general knowledge."""

_GROUNDING_RULES_NON_CURRICULUM = """This is a general-knowledge question with no textbook context
available. Answer at the exact cognitive level of the stated grade, using relatable Indian
real-world examples and everyday analogies. Encourage curiosity for asking questions beyond
the syllabus."""

_OUTPUT_RULES = """OUTPUT RULES:
- NEVER start text_narration with a greeting ("Namaste", "Hello", "Hey") or the student's name.
  Start immediately with the direct educational answer.
- Default text_narration shape is a bulleted list, not a paragraph, unless the question itself
  asks for a different shape (e.g. "explain in detail" -> numbered steps, "as a story" ->
  narrative/analogy framing).
- text_narration must not contain markdown code fences.
- CRITICAL JSON ESCAPING: text_narration frequently contains LaTeX (\\frac{a}{b}, \\times,
  \\left(...\\right)). Every backslash must be escaped as a DOUBLE backslash in the JSON string,
  or the response will fail to parse. Only \\n and \\" may use a single backslash."""

_PROMPT_TEMPLATE = """{persona}

STUDENT_GRADE: Class {grade}
CURRENT_DATE: {current_date}

{format_rules}

{grounding_rules}

{output_rules}

QUESTION:
{question}

TEXTBOOK CONTEXT (may be empty for a non-curriculum question):
{context}

Return ONLY a single JSON object with this exact shape, no markdown fences, no commentary:
{{
  "format_decision": "QUICK_ANSWER" | "VIDEO_REQUIRED",
  "text_narration": string | null
}}
"""


def build_prompt(question: str, grade: int, context: str, is_curriculum: bool) -> str:
    return _PROMPT_TEMPLATE.format(
        persona=_PERSONA,
        grade=grade,
        current_date=datetime.datetime.now().strftime("%A, %B %d, %Y"),
        format_rules=_FORMAT_RULES,
        grounding_rules=_GROUNDING_RULES_CURRICULUM if is_curriculum else _GROUNDING_RULES_NON_CURRICULUM,
        output_rules=_OUTPUT_RULES,
        question=question,
        context=context or "(none)",
    )


def _repair_invalid_escapes(text: str) -> str:
    return re.sub(
        r'\\(.)',
        lambda m: m.group(0) if m.group(1) in ('"', '\\') else '\\\\' + m.group(1),
        text,
    )


def _extract_json(text: str) -> Dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    candidate = text[start:end + 1] if start != -1 and end != -1 and end > start else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return json.loads(_repair_invalid_escapes(candidate))


def generate_answer(
    question: str, grade: int, context: str, is_curriculum: bool, openai_client, model_name: str,
    llm_calls: Optional[List] = None,
) -> Dict:
    prompt = build_prompt(question, grade, context, is_curriculum)
    result = call_llm(
        openai_client, model_name, prompt, stage="answer_generation",
        config={"temperature": 0.2},
        extra={"is_curriculum": is_curriculum, "context_chars": len(context or "")},
    )
    if llm_calls is not None:
        llm_calls.append(result)

    if result.error:
        logger.warning(f"[QUESTION_PIPELINE][Generation] LLM call failed: {result.error}")
        return {"format_decision": "QUICK_ANSWER", "text_narration": "Sorry, I couldn't generate an answer right now. Please try again."}

    try:
        parsed = _extract_json(result.text)
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Generation] failed to parse LLM response: {e}")
        return {"format_decision": "QUICK_ANSWER", "text_narration": "Sorry, I couldn't generate an answer right now. Please try again."}

    format_decision = parsed.get("format_decision")
    if format_decision not in ("QUICK_ANSWER", "VIDEO_REQUIRED"):
        format_decision = "QUICK_ANSWER" if parsed.get("text_narration") else "VIDEO_REQUIRED"

    text_narration = parsed.get("text_narration")
    if format_decision == "QUICK_ANSWER" and not (text_narration or "").strip():
        # Contract violation, found live 2026-09-01: per the FORMAT SELECTION
        # rule above, text_narration is only ever supposed to be null for
        # VIDEO_REQUIRED - but the model sometimes returns a syntactically
        # valid "QUICK_ANSWER" with a null narration anyway when it has
        # nothing concrete to answer (e.g. a vague reference with no real
        # textbook context and no prior turn that actually resolves it).
        # The repair branch above only catches a missing/invalid
        # format_decision, not a self-consistent-looking but empty one, so
        # this was passing straight through as a silent blank answer to the
        # student. Surface it as a clarification ask instead of trusting the
        # self-reported format_decision blindly.
        logger.warning("[QUESTION_PIPELINE][Generation] QUICK_ANSWER returned with empty text_narration - falling back to clarification ask")
        text_narration = "I'm not fully sure what you're asking here - could you share a bit more detail, like the subject, chapter, or the actual question you'd like help with?"

    return {"format_decision": format_decision, "text_narration": text_narration}


# --- web-search path (non-curriculum, live/current-events questions only) ---------

def generate_web_search_answer(
    question: str, grade: int, board: str, conversation_context: List[Dict],
    openai_client, model_name: str,
    llm_calls: Optional[List] = None,
) -> Dict:
    """
    Ported from test_runner.py's is_gk_query branch, unchanged in substance:
    a small, dedicated prompt (not the persona/format prompt above) with a
    forced, literal search string built in code rather than left to the
    model - live testing found a soft hint wasn't reliably followed. Always
    returns QUICK_ANSWER, text+audio, per Stage 6's fixed rule for this path.
    """
    current_date_time = datetime.datetime.now().strftime("%A, %B %d, %Y (%H:%M:%S)")
    current_year = datetime.datetime.now().year

    prior_turn = conversation_context[-1] if conversation_context else None
    conversation_summary = "none - this is the first turn on this topic in this conversation"
    if prior_turn:
        conversation_summary = (
            f"Student just asked: \"{prior_turn.get('query') or prior_turn.get('question') or ''}\"\n"
            f"You just answered: \"{(prior_turn.get('answer') or '')[:250]}\""
        )

    system_prompt = (
        f"You are answering a general-knowledge question for a Class {grade} Indian student "
        f"(board: {board or 'CBSE'}). Today's real date is {current_date_time}. Treat this as "
        "ground truth even if it feels later than your own training data.\n\n"
        f"CONVERSATION SO FAR: {conversation_summary}\n"
        "If the student's question uses a pronoun or vague reference, resolve it using ONLY the "
        "conversation above - do not silently pick a different topic just because it's more recent "
        "or famous. If it genuinely can't be resolved, ask for clarification instead of guessing.\n\n"
        "Use web search to find the current, accurate answer before replying. Preserve every "
        "specific qualifier in the question exactly (sport, competition, country, person, party). "
        "If asking about the 'latest'/'most recent' edition of a recurring event, your search terms "
        "must include the current year AND the word 'latest'. State the exact date/year of the "
        "edition you're reporting on. Answer directly and concisely (3-6 sentences), simple grade-"
        "appropriate language, no storytelling framing - this is a factual lookup."
    )
    forced_search_text = f"{question} {current_year}"
    user_prompt = (
        f"Your first web search tool call MUST use exactly this text as the query, verbatim: "
        f"\"{forced_search_text}\"\n\nIf inconclusive, search again with \"{question} {current_year - 1}\".\n\n"
        f"USER RAW QUERY: \"{question}\""
    )

    result = call_llm(
        openai_client, model_name, [system_prompt, user_prompt], stage="answer_generation_web_search",
        config={"temperature": 0.0, "web_search": True},
        extra={"grade": grade, "board": board},
    )
    if llm_calls is not None:
        llm_calls.append(result)

    if result.error:
        logger.warning(f"[QUESTION_PIPELINE][Generation][WebSearch] LLM call failed: {result.error}")
        text = "Sorry, I couldn't look that up right now. Please try again."
    else:
        text = (result.text or "").strip()

    return {"format_decision": "QUICK_ANSWER", "text_narration": text}
