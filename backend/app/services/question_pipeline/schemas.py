"""
Typed stage contracts for the Question Understanding & Routing layer.

Revised 2026-08-30 per docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md: curriculum
match is no longer an LLM opinion produced alongside reformulation - it is a
separate, deterministic decision (CurriculumDecision) made from a real RAG
call at Stage 4. Safety is its own three-layer result (SafetyResult), not a
field embedded in the understanding call's output.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Question-validity classes (is this even a real, answerable question) -
# distinct from curriculum matching, which is a separate concern (see
# CurriculumDecision below).
VALIDATION_CLASSES = ("VALID", "FOLLOW_UP", "AMBIGUOUS", "INVALID", "UNSUPPORTED")

# Controlled intent enum - do not let the LLM invent others.
INTENTS = (
    "DEFINE", "EXPLAIN", "WHY", "HOW", "SOLVE", "CALCULATE", "COMPARE",
    "SUMMARIZE", "EXAMPLE", "PRACTICE", "ASSESS", "REVISE", "FOLLOW_UP", "CLARIFY",
)

# Narrowed 2026-08-30 (Decision 6): this pipeline only ever routes Direct
# Question traffic - NORMAL or FOLLOW_UP. ASSESSMENT/CALCULATION/IMAGE
# request types get their own entry points elsewhere in the app, not
# free-text detection here.
ROUTES = ("NORMAL", "FOLLOW_UP")

CURRICULUM_MATCH_VALUES = ("in_grade", "not_in_curriculum")


@dataclass
class PipelineInput:
    """Only relevant slices should be filled in, not the entire learner
    profile / entire conversation history."""
    student_question: str
    conversation_context: List[Dict] = field(default_factory=list)  # trimmed recent turns
    learner_context: Dict = field(default_factory=dict)             # e.g. response_style, class, board, language
    session_context: Dict = field(default_factory=dict)             # e.g. book_uuid, subject, class_name
    session_id: Optional[str] = None
    uid: str = ""  # real student uid - required for Stage 8's personal History write


@dataclass
class SafetyResult:
    """Output of the three safety layers (safety.py). is_safe=False on any
    hard block from any layer - refusal_reason is always set in that case."""
    is_safe: bool
    refusal_reason: Optional[str] = None
    layer1_jailbreak_detected: bool = False
    layer1_academic_integrity_detected: bool = False
    layer1_default_allowed_followup: bool = False
    layer2_categories_flagged: List[str] = field(default_factory=list)
    layer2_borderline: bool = False
    layer3_ran: bool = False
    layer3_result: Optional[str] = None  # "resolved_safe" | "still_unsafe" | None


@dataclass
class ValidationResult:
    classification: str  # one of VALIDATION_CLASSES
    reason: str = ""
    clarification_prompt: Optional[str] = None


@dataclass
class ResolvedQuestion:
    original_question: str
    resolved_question: str
    used_follow_up: bool = False
    resolution_reason: str = ""


@dataclass
class IntentResult:
    primary_intent: str  # one of INTENTS
    secondary_intent: Optional[str] = None
    confidence: float = 0.0


@dataclass
class CurriculumGuess:
    """Stage 2's output about subject/topic - a grounded GUESS (matched
    against real cached curriculum metadata), never a verdict. Whether the
    question is actually curriculum is decided later, at Stage 4, by
    CurriculumDecision below."""
    class_name: str = ""
    subject: str = ""
    chapter: str = ""
    topic: str = ""
    concept: str = ""
    book_uuid: str = ""
    chapter_id: Optional[str] = None
    guess_confidence: float = 0.0


@dataclass
class ReformulatedQuery:
    original_question: str
    resolved_question: str
    semantic_query: str
    keyword_query: str
    metadata_filters: Dict = field(default_factory=dict)


@dataclass
class RoutingDecision:
    route: str  # one of ROUTES
    reason: str = ""


@dataclass
class RAGResult:
    context: str
    sources: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    confidence_tier: Optional[str] = None
    retrieval_status: str = "not_attempted"
    raw: Dict = field(default_factory=dict)


@dataclass
class CurriculumDecision:
    """Stage 4's output - the ONLY place curriculum_match is decided, and
    always from a real RAG call's confidence_tier, never an LLM opinion."""
    curriculum_match: str  # one of CURRICULUM_MATCH_VALUES
    decided_at_stage: str = "4"  # "4" (normal path) | "1c" (borderline-safety early path)
    rag: Optional[RAGResult] = None


@dataclass
class ContextValidationResult:
    is_sufficient: bool
    reason: str = ""


@dataclass
class GroundingResult:
    is_grounded: bool
    overlap_ratio: float = 0.0
    unsupported_sentences: List[str] = field(default_factory=list)
    class_appropriate: bool = True
    reason: str = ""


@dataclass
class PipelineResult:
    """What the pipeline hands back to the caller (e.g. chat.py)."""
    final_answer: str
    status: str  # ANSWERED | CLARIFICATION_NEEDED | REFUSED | INSUFFICIENT_CONTEXT
    request_id: Optional[str] = None  # this call's pipeline_logs doc id - lets a
    # caller attach post-hoc data (TTS latency, video storyboard) that only
    # exists AFTER this call returns, via log_store.update_pipeline_log()
    safety: Optional[SafetyResult] = None
    validation: Optional[ValidationResult] = None
    resolved: Optional[ResolvedQuestion] = None
    intent: Optional[IntentResult] = None
    curriculum_guess: Optional[CurriculumGuess] = None
    curriculum_decision: Optional[CurriculumDecision] = None
    rag_result: Optional[RAGResult] = None  # Stage 5's ACTUAL fetch result used for
    # generation - distinct from curriculum_decision.rag (Stage 4's raw search).
    # These differ whenever a follow-up reuses cached chunks instead of Stage 4's
    # fresh result - added 2026-09-01 after finding callers had no way to see
    # what was actually used for generation on a cache-reuse turn.
    query: Optional[ReformulatedQuery] = None
    routing: Optional[RoutingDecision] = None
    format_decision: Optional[str] = None  # QUICK_ANSWER | VIDEO_REQUIRED
    context_validation: Optional[ContextValidationResult] = None
    grounding: Optional[GroundingResult] = None
    trace: List[str] = field(default_factory=list)  # fault-isolation breadcrumbs, one line per stage
