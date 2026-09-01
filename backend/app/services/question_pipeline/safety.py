"""
Stage 1 of the FRD v3 flow - three layers, run before anything else, per
docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md section 3:

  Layer 1 (this module, check_layer1_rules): deterministic pattern matching,
    no API call. Jailbreak-phrase detection ported from the current
    master_orchestrator_prompt.txt Section 2.2 phrase list; academic-integrity
    detection (asking for direct test/exam/homework answers, or someone else
    to do the work) added 2026-09-01 after live testing showed such requests
    were reaching Understanding + Curriculum Decision (a ~48s multi-book
    fallback search) and only getting refused by the answer_generation LLM's
    own judgment at the very end - full pipeline cost for a request neither
    OpenAI moderation (not a moderation category) nor the jailbreak patterns
    (scoped to instruction-override phrasing, not "give me the answers")
    were ever going to catch; default-allow for short vague follow-ups.
  Layer 2 (this module, check_layer2_moderation): OpenAI's free,
    non-generative omni-moderation-latest classifier on the raw question.
    Clear categories hard-block immediately; ambiguous ones (where
    curriculum content like reproductive biology or political civics tends
    to land) are marked borderline, not blocked.
  Layer 3 (this module, residual_recheck): ONLY runs if Layer 2 marked the
    request borderline. Makes an early, out-of-order call into Stage 4's
    real RAG-based curriculum check (rag_stage.decide_curriculum) to
    resolve the ambiguity using genuine retrieved content - a real syllabus
    match is treated as safe, still-borderline-and-not-in-curriculum is a
    hard block. The common path (Layer 2 clean) never reaches this layer
    and is completely unaffected by its cost.

No LLM generation call happens anywhere in this module - Layer 2 is a
classifier (scores, not text), Layer 1 is regex, Layer 3 delegates to a
retrieval call, not a judgment call.
"""
import logging
import os
import re
from typing import Dict, List, Optional

from backend.app.services.question_pipeline.schemas import SafetyResult

logger = logging.getLogger(__name__)

_REFUSAL_MESSAGE = (
    "I am here to help you learn, but I cannot assist with this request. "
    "If you have another educational or curriculum question, I would be happy to help!"
)

# --- Layer 1: deterministic rules -------------------------------------------------

# Ported from master_orchestrator_prompt.txt Section 2.2's jailbreak phrase
# list - pattern match against the shape of a known override attempt, not an
# exhaustive list (see docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md section 6
# for the accepted limitation: a rephrasing that avoids all of these is not
# caught by this layer).
_JAILBREAK_PATTERNS = [
    # NOTE (2026-08-30): found live testing that a `?` (zero-or-one) on this
    # alternation group missed "ignore all previous instructions" - two
    # qualifier words in front of "instructions", not one. `*` (zero-or-more)
    # allows the group to repeat, matching any combination of qualifiers.
    r"ignore\s+(all\s+|your\s+|previous\s+)*instructions",
    r"disregard\s+(all\s+|your\s+)*instructions",
    r"pretend\s+you\s+are\s+(an?\s+)?(ai\s+)?(with\s+)?(no|zero)\s+(rules|restrictions)",
    r"pretend\s+you\s+(have\s+)?no\s+(rules|restrictions)",
    r"act\s+as\s+(a\s+)?(hacker|dan|uncensored)",
    r"you\s+are\s+no\s+longer\s+(chaduvu\s+guru|an?\s+study\s+assistant)",
    r"system\s+override",
    r"as\s+an\s+administrator[,]?\s+i(\s*'?m|\s+am)\s+authoriz",
    r"you\s+are\s+authorized\s+to\s+(bypass|ignore)",
    r"for\s+(research|story|storywriting)\s+purposes[,]?\s+ignore",
]
_JAILBREAK_RE = re.compile("|".join(_JAILBREAK_PATTERNS), re.IGNORECASE)

# Requests for someone/something else to directly produce graded work -
# not a jailbreak (no attempt to override the assistant's rules) and not a
# moderation category (OpenAI's moderation model has no "cheating" class),
# so it needs its own deterministic check. Scoped to "answers to a
# test/exam/etc" and "do my homework for me" shapes, not general homework
# help ("help me with my homework", "explain question 3") which must stay
# allowed.
_ACADEMIC_INTEGRITY_PATTERNS = [
    r"answers?\s+(to|for)\s+.{0,40}?\b(test|exam|quiz|assignment|homework)\b",
    r"\b(test|exam|quiz)\s+(paper\s+)?(questions?\s+)?(in\s+advance|before(hand)?|leak)",
    r"(do|complete|finish|write|solve)\s+my\s+(?:\w+\s+){0,2}(homework|assignment|test|exam|essay|project|paper)\s+for\s+me",
]
_ACADEMIC_INTEGRITY_RE = re.compile("|".join(_ACADEMIC_INTEGRITY_PATTERNS), re.IGNORECASE)

_ACADEMIC_INTEGRITY_REFUSAL = (
    "I can't give you direct answers to a test, exam, or assignment, or do it for you - "
    "that wouldn't actually help you learn, and it could count as academic dishonesty. "
    "I'm glad to help you understand the concepts so you're ready to answer it yourself!"
)

# A short, vague follow-up with a prior turn to resolve against is never
# suspicious - per master_orchestrator_prompt.txt Section 2, directive 2B.
_MAX_WORDS_FOR_DEFAULT_ALLOW = 8


def check_layer1_rules(question: str, conversation_context: List[Dict]) -> SafetyResult:
    question = (question or "").strip()
    jailbreak_detected = bool(_JAILBREAK_RE.search(question))
    if jailbreak_detected:
        return SafetyResult(is_safe=False, refusal_reason=_REFUSAL_MESSAGE, layer1_jailbreak_detected=True)

    academic_integrity_detected = bool(_ACADEMIC_INTEGRITY_RE.search(question))
    if academic_integrity_detected:
        return SafetyResult(
            is_safe=False,
            refusal_reason=_ACADEMIC_INTEGRITY_REFUSAL,
            layer1_academic_integrity_detected=True,
        )

    is_short = len(question.split()) <= _MAX_WORDS_FOR_DEFAULT_ALLOW
    has_prior_turn = bool(conversation_context)
    default_allowed = is_short and has_prior_turn

    return SafetyResult(is_safe=True, layer1_default_allowed_followup=default_allowed)


# --- Layer 2: OpenAI moderation classifier ----------------------------------------

# Categories where a hit is unambiguous - no curriculum content plausibly
# lands here, so these hard-block immediately.
_HARD_BLOCK_CATEGORIES = {
    "self-harm", "self-harm/intent", "self-harm/instructions",
    "illicit", "illicit/violent",
    "hate", "hate/threatening", "harassment/threatening",
    "sexual/minors",
}
# Categories where curriculum content (reproductive biology, historical/
# civics violence, personal-framed body questions) can plausibly score -
# these are marked borderline, not blocked, and resolved by Layer 3.
_BORDERLINE_CATEGORIES = {"violence", "violence/graphic", "sexual", "harassment"}

_moderation_client = None


def _get_moderation_client():
    """Own, minimal OpenAI client for moderation only - the app's compat
    wrapper (llm/openai_client.py) doesn't expose the raw .moderations
    resource, and moderation calls don't need that wrapper's generate_content
    shape at all."""
    global _moderation_client
    if _moderation_client is None:
        from openai import OpenAI
        _moderation_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _moderation_client


def check_layer2_moderation(question: str) -> SafetyResult:
    question = (question or "").strip()
    if not question:
        return SafetyResult(is_safe=True)

    try:
        client = _get_moderation_client()
        response = client.moderations.create(model="omni-moderation-latest", input=question)
        result = response.results[0]
        # Verified live 2026-08-30: the SDK's categories object always
        # populates BOTH a hyphen/slash form ("self-harm/intent") AND an
        # underscore alias ("self_harm_intent") for the same signal,
        # identically - not two different signals. Reporting only the
        # canonical hyphen/slash form (which _HARD_BLOCK_CATEGORIES and
        # _BORDERLINE_CATEGORIES already match against) avoids listing the
        # same flagged category twice under two different-looking names.
        raw_flagged = {cat for cat, is_flagged in result.categories.model_dump().items() if is_flagged}
        flagged_categories = [c for c in raw_flagged if "_" not in c]
    except Exception as e:
        # Fail open - a moderation-endpoint outage must never silently take
        # the whole app down. Layer 1 rules still ran; Layer 3 simply never
        # triggers for this turn since nothing was marked borderline.
        logger.warning(f"[SAFETY][Layer2] Moderation call failed, failing open: {e}")
        return SafetyResult(is_safe=True)

    hard_block_hits = [c for c in flagged_categories if c in _HARD_BLOCK_CATEGORIES]
    if hard_block_hits:
        return SafetyResult(
            is_safe=False,
            refusal_reason=_REFUSAL_MESSAGE,
            layer2_categories_flagged=flagged_categories,
        )

    borderline_hits = [c for c in flagged_categories if c in _BORDERLINE_CATEGORIES]
    if borderline_hits:
        return SafetyResult(
            is_safe=True,
            layer2_categories_flagged=flagged_categories,
            layer2_borderline=True,
        )

    return SafetyResult(is_safe=True, layer2_categories_flagged=flagged_categories)


# --- Layer 3: residual-band recheck -------------------------------------------------

def residual_recheck(curriculum_guess, grade: int) -> SafetyResult:
    """
    Only called when Layer 2 marked the request borderline. Makes an early
    call into rag_stage.decide_curriculum() (Stage 4's real, deterministic
    RAG-based curriculum decision) to resolve the ambiguity - real
    retrieved content backing the guessed subject/topic means safe, still
    nothing found means still unsafe. The caller (pipeline.py) reuses this
    same CurriculumDecision at Stage 4's normal position later, rather than
    searching again.
    """
    from backend.app.services.question_pipeline import rag_stage  # lazy: avoid import cycle

    decision = rag_stage.decide_curriculum(curriculum_guess, grade, decided_at_stage="1c")

    if decision.curriculum_match == "in_grade":
        return SafetyResult(
            is_safe=True,
            layer3_ran=True,
            layer3_result="resolved_safe",
        ), decision

    return SafetyResult(
        is_safe=False,
        refusal_reason=_REFUSAL_MESSAGE,
        layer3_ran=True,
        layer3_result="still_unsafe",
    ), decision
