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

# Ported 2026-09-02 from master_orchestrator_prompt.txt SECTION 3 (directives
# 3/4/5/6/7/8) verbatim in substance, per explicit instruction: personalized
# learning must keep shaping the answer exactly as it does today while only
# the orchestrator's decision-making gets replaced. Directive 1B (resolving
# "that"/"it" against the immediate prior turn) is NOT duplicated here - it's
# already handled structurally by understanding.py's _format_conversation,
# which labels the MOST RECENT TURN explicitly. Directive 2 (matching
# CURRICULUM_DATA) is Stage 2/4's job in this architecture, not Stage 6's.
_PERSONALIZATION_RULES = """PERSONALIZATION CONTEXT (optional - "not set"/"none" for a student with no
history yet; never block an answer on missing personalization data):
- RESPONSE_STYLE_PREFERENCE : {response_style}
- SKILL_ENGAGEMENT_QUADRANT : {quadrant}
- REPEAT_QUESTION_ESCALATION: {escalation_instruction}
- THIS_STUDENT_PRIOR_HISTORY: {per_student_memory_context}
- TOUGH_EASY_SUBJECTS_NOTE  : {tough_easy_subjects_note}

PERSONALIZATION DIRECTIVES:
1. CRITICAL, MANDATORY - RESPONSE_STYLE_PREFERENCE shapes HOW you deliver the
   answer (not what's correct), and TAKES PRECEDENCE over the default bulleted-list
   format above whenever they'd conflict:
   - "storytelling": text_narration MUST open with or be built around a concrete
     narrative/analogy sentence. Plain bulleted facts with NO analogy/narrative
     element is NOT storytelling and is a directive violation.
   - "direct": lead with the answer itself, minimal preamble - the default
     bulleted-list format is appropriate here.
   - "detailed": text_narration MUST use explicit numbered steps (1. 2. 3. ...),
     NOT flowing prose and NOT unordered bullets.
   - If "not set", default to the bulleted-list format.
   - OVERRIDE RULE (HIGHEST PRIORITY): if THIS query itself explicitly asks for a
     different shape ("give me a quick answer," "explain in detail," "as bullet
     points," "storytelling analogy") than RESPONSE_STYLE_PREFERENCE, the query's
     explicit request wins for THIS turn only.
2. If TOUGH_EASY_SUBJECTS_NOTE names a subject the student finds tough and this
   query is in that subject, add slightly more scaffolding/concrete examples than
   SKILL_ENGAGEMENT_QUADRANT alone would suggest. If it names a subject as easy,
   move a bit faster / assume more. If "not set," ignore this rule.
3. SKILL_ENGAGEMENT_QUADRANT informs depth and pacing, never gatekeeps content:
   - "high_skill_*": may go slightly beyond the bare grade-level minimum when relevant.
   - "low_skill_*": favor more concrete examples and simpler vocabulary before nuance.
   - "*_low_engagement": keep the answer tighter, end with an inviting, low-effort next step.
4. Judge grade_relative_difficulty by comparing this query's conceptual depth against
   STUDENT_GRADE's typical academic ability, not by keyword-spotting - this only
   affects tone/scaffolding here, it is never a reason to refuse or defer the answer.
5. If REPEAT_QUESTION_ESCALATION is not "none", do NOT repeat the same explanation
   shape as a likely prior answer - prefer a concrete example/analogy over a restated
   definition, or suggest a diagram/visual framing if one would help.
6. If THIS_STUDENT_PRIOR_HISTORY is non-empty, it lists topics/questions this SAME
   student was already taught or gave feedback on. If the current query is related,
   explicitly build on that prior turn instead of re-explaining from scratch - this
   is the single most important personalization directive, since ignoring known
   prior context on a follow-up is the exact failure this exists to fix.
   - Any line marked "MANDATORY FEEDBACK REQUIREMENT" is a hard constraint on this
     answer, not background to weigh against other considerations - it takes
     priority over directive 1's RESPONSE_STYLE_PREFERENCE whenever they'd conflict
     (e.g. if honoring it requires a worked example, include one even in an
     otherwise short answer). Before finalizing text_narration, re-check it actually
     satisfies the specific complaint stated, not just a generic improvement."""

_PROMPT_TEMPLATE = """{persona}

STUDENT_GRADE: Class {grade}
CURRENT_DATE: {current_date}

{format_rules}

{grounding_rules}

{personalization}

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


def _format_personalization(learner_context: Optional[Dict], raw_question: str) -> Dict:
    """
    Ported 2026-09-02 from test_runner.py's run_orchestrator_pipeline (lines
    ~524-619) - same field semantics, same "not set"/"none" fallbacks, same
    MANDATORY FEEDBACK REQUIREMENT detection - so behavior matches the old
    live flow exactly, not just approximately. Returns the formatted prompt
    block plus the two flags the restyle pass below needs:
    mandatory_feedback_active (skip restyle if true) and
    effective_style (inline query override > stored preference).
    """
    lc = learner_context or {}
    response_style = lc.get("response_style") or "not set"
    quadrant = lc.get("quadrant") or "not set"
    escalation_level = lc.get("escalation_level", 0) or 0
    if escalation_level >= 2:
        escalation_instruction = f"student has repeated a similar basic question {escalation_level} times on this topic - escalate strongly (favor a diagram/visual framing)"
    elif escalation_level == 1:
        escalation_instruction = "student repeated a similar basic question once on this topic - escalate to a concrete example/analogy"
    else:
        escalation_instruction = "none"

    per_student_history = lc.get("per_student_history") or []
    mandatory_feedback_active = False
    if per_student_history:
        history_lines = []
        for h in per_student_history[:3]:
            q = h.get("reformulated_question") or h.get("question") or ""
            if h.get("is_feedback"):
                fb_type = h.get("feedback_type") or "negative"
                reason = (h.get("feedback_reason") or "").strip()
                if fb_type == "negative":
                    if reason:
                        mandatory_feedback_active = True
                        history_lines.append(
                            f"- MANDATORY FEEDBACK REQUIREMENT: this student DISLIKED a previous "
                            f"explanation of \"{q}\" and said why: \"{reason}\". This is a binding "
                            f"requirement for THIS answer, not optional context - you MUST concretely "
                            f"address that specific complaint. Do not just acknowledge it - fix it."
                        )
                    else:
                        history_lines.append(
                            f"- FEEDBACK: this student DISLIKED a previous explanation of \"{q}\" "
                            f"(no reason given). Try a genuinely different approach/angle."
                        )
                else:
                    reason_clause = f" (reason given: \"{reason}\")" if reason else ""
                    history_lines.append(
                        f"- FEEDBACK: this student LIKED a previous explanation of \"{q}\"{reason_clause}. "
                        f"A similar approach worked well for them."
                    )
            else:
                summary = (h.get("answer_summary") or "")[:200]
                history_lines.append(f"- Previously asked: \"{q}\" -> covered: {summary}")
        per_student_memory_context = "\n".join(history_lines)
    else:
        per_student_memory_context = "none - this is either the student's first question on this topic, or no related prior history was found"

    tough_subjects = lc.get("tough_subjects") or []
    easy_subjects = lc.get("easy_subjects") or []
    subject_notes = []
    if tough_subjects:
        subject_notes.append(f"finds these subjects tough: {', '.join(tough_subjects)}")
    if easy_subjects:
        subject_notes.append(f"finds these subjects easy: {', '.join(easy_subjects)}")
    tough_easy_subjects_note = "; ".join(subject_notes) if subject_notes else "not set"

    formatted = _PERSONALIZATION_RULES.format(
        response_style=response_style, quadrant=quadrant, escalation_instruction=escalation_instruction,
        per_student_memory_context=per_student_memory_context, tough_easy_subjects_note=tough_easy_subjects_note,
    )
    effective_style = detect_inline_style_override(raw_question) or (response_style if response_style != "not set" else None)
    return {"text": formatted, "mandatory_feedback_active": mandatory_feedback_active, "effective_style": effective_style}


def detect_inline_style_override(query: str) -> Optional[str]:
    """Ported verbatim (test_runner.py) - "a stated preference is a default,
    not a lock": inline query intent always wins over the stored preference."""
    q = (query or "").lower()
    if any(p in q for p in ("step by step", "step-by-step", "in detail", "in-depth", "detailed")):
        return "detailed"
    if any(p in q for p in ("story", "analogy", "storytelling", "like a story")):
        return "storytelling"
    if any(p in q for p in ("quick answer", "quickly", "brief", "short answer", "just the answer", "direct answer")):
        return "direct"
    return None


def restyle_text_narration(text: str, style: str, client, model: str) -> Optional[str]:
    """
    Ported verbatim (test_runner.py) - a second, narrowly-scoped corrective
    LLM call whose ONLY job is restyling already-correct text. Exists
    because live testing found a small model doesn't reliably prioritize a
    stylistic instruction over factual/grounding ones when both compete in
    one call. Only rewrites PRESENTATION - never adds/removes/changes a
    fact/number/formula. Returns None (caller keeps the original) on any
    failure - quality enhancement, never allowed to block or corrupt an answer.
    """
    if style == "storytelling":
        instruction = (
            "Rewrite this into a STORYTELLING style: open with ONE concrete narrative or "
            "analogy sentence that frames the concept (e.g. a relatable everyday scenario), "
            "then weave the existing facts into flowing prose around that analogy. "
            "Do not use bullet points. Do not lose or alter any fact, number, or formula."
        )
    elif style == "detailed":
        instruction = (
            "Rewrite this into a DETAILED style: explicit numbered steps (1. 2. 3. ...), "
            "each step a complete sentence. Do not use unordered bullets or a single paragraph. "
            "Do not lose or alter any fact, number, or formula."
        )
    else:
        return None

    prompt = f"{instruction}\n\nOriginal text:\n{text}\n\nRewritten text (same facts, new style, no preamble/commentary, just the rewritten text itself):"
    try:
        response = client.models.generate_content(model=model, contents=[prompt], config={"temperature": 0.4})
        rewritten = (response.text or "").strip()
        return rewritten if rewritten else None
    except Exception as e:
        logger.warning(f"[QUESTION_PIPELINE][Generation][Restyle] LLM call failed: {e}")
        return None


def apply_personalized_restyle(result: Dict, learner_context: Optional[Dict], raw_question: str, openai_client, model_name: str) -> Dict:
    """
    Call once after generate_answer/generate_web_search_answer returns -
    single shared application point so every text-producing path gets the
    same restyle treatment the old flow applied uniformly, per explicit
    instruction that personalization must keep behaving the same way.
    Video answers (text_narration is null) are skipped, same as the old
    flow's own format_decision == "QUICK_ANSWER" gate.
    """
    if result.get("format_decision") != "QUICK_ANSWER" or not result.get("text_narration"):
        return result
    personalization = _format_personalization(learner_context, raw_question)
    if personalization["mandatory_feedback_active"]:
        logger.info("[QUESTION_PIPELINE][Generation][Restyle] Skipped - MANDATORY FEEDBACK REQUIREMENT active this turn.")
        return result
    style = personalization["effective_style"]
    if style not in ("storytelling", "detailed"):
        return result
    restyled = restyle_text_narration(result["text_narration"], style, openai_client, model_name)
    if restyled:
        result = {**result, "text_narration": restyled}
    return result


def build_prompt(question: str, grade: int, context: str, is_curriculum: bool, learner_context: Optional[Dict] = None) -> str:
    return _PROMPT_TEMPLATE.format(
        persona=_PERSONA,
        grade=grade,
        current_date=datetime.datetime.now().strftime("%A, %B %d, %Y"),
        format_rules=_FORMAT_RULES,
        grounding_rules=_GROUNDING_RULES_CURRICULUM if is_curriculum else _GROUNDING_RULES_NON_CURRICULUM,
        personalization=_format_personalization(learner_context, question)["text"],
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
    llm_calls: Optional[List] = None, learner_context: Optional[Dict] = None,
) -> Dict:
    prompt = build_prompt(question, grade, context, is_curriculum, learner_context)
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
