"""
Typed stage contracts for the Question Understanding & Routing layer
(D:\\05 DronaX - Next Implementation After RAG.pdf, sections 3-11).

Each stage takes and returns one of these dataclasses so the pipeline stays
"discrete and testable" per the doc's section 16 ("do not put all logic into
one giant LLM prompt") rather than one big free-form dict passed around.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Section 4: controlled validation classes - do not let the LLM invent others.
VALIDATION_CLASSES = ("VALID", "FOLLOW_UP", "AMBIGUOUS", "INVALID", "UNSUPPORTED")

# Section 5: controlled intent enum - do not let the LLM invent others.
INTENTS = (
    "DEFINE", "EXPLAIN", "WHY", "HOW", "SOLVE", "CALCULATE", "COMPARE",
    "SUMMARIZE", "EXAMPLE", "PRACTICE", "ASSESS", "REVISE", "FOLLOW_UP", "CLARIFY",
)

# Section 9: controlled route names.
ROUTES = (
    "NORMAL", "FOLLOW_UP", "AMBIGUOUS", "ASSESSMENT", "CALCULATION", "IMAGE", "UNSUPPORTED",
)


@dataclass
class PipelineInput:
    """Section 3 request contract. Only relevant slices should be filled in,
    not the entire learner profile / entire conversation history."""
    student_question: str
    conversation_context: List[Dict] = field(default_factory=list)  # trimmed recent turns
    learner_context: Dict = field(default_factory=dict)             # e.g. response_style, class, subject
    session_context: Dict = field(default_factory=dict)             # e.g. book_uuid, subject, class_name
    requested_response_type: str = ""


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
class CurriculumContext:
    class_name: str = ""
    subject: str = ""
    chapter: str = ""
    topic: str = ""
    concept: str = ""
    book_uuid: str = ""
    chapter_id: Optional[str] = None
    confidence: float = 0.0


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
    validation: Optional[ValidationResult] = None
    resolved: Optional[ResolvedQuestion] = None
    intent: Optional[IntentResult] = None
    curriculum: Optional[CurriculumContext] = None
    query: Optional[ReformulatedQuery] = None
    routing: Optional[RoutingDecision] = None
    rag: Optional[RAGResult] = None
    context_validation: Optional[ContextValidationResult] = None
    grounding: Optional[GroundingResult] = None
    trace: List[str] = field(default_factory=list)  # section 20 fault-isolation breadcrumbs
