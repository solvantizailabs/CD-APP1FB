"""
Stage 7 (spec section 9): Query Routing.

Deterministic, no LLM call - "the router's responsibility is only to decide
which path to use. It should not duplicate the RAG implementation." Routing
is derived from the already-computed validation classification and intent,
not from re-reading the raw question text.
"""
from backend.app.services.question_pipeline.schemas import (
    IntentResult,
    RoutingDecision,
    ValidationResult,
)

_ASSESSMENT_INTENTS = {"ASSESS", "PRACTICE"}
_CALCULATION_INTENTS = {"SOLVE", "CALCULATE"}


def route_question(
    validation: ValidationResult,
    intent: IntentResult,
    has_image: bool = False,
) -> RoutingDecision:
    if has_image:
        return RoutingDecision(route="IMAGE", reason="Input includes an image/document attachment.")

    if validation.classification == "UNSUPPORTED":
        return RoutingDecision(route="UNSUPPORTED", reason=validation.reason or "Out of scope for this system.")

    if validation.classification in ("AMBIGUOUS", "INVALID"):
        return RoutingDecision(route="AMBIGUOUS", reason=validation.reason or "Question needs clarification.")

    if intent.primary_intent in _ASSESSMENT_INTENTS:
        return RoutingDecision(route="ASSESSMENT", reason=f"Intent {intent.primary_intent} maps to assessment route.")

    if intent.primary_intent in _CALCULATION_INTENTS:
        return RoutingDecision(route="CALCULATION", reason=f"Intent {intent.primary_intent} maps to calculation route.")

    if validation.classification == "FOLLOW_UP":
        return RoutingDecision(route="FOLLOW_UP", reason="Depends on prior conversation turn.")

    return RoutingDecision(route="NORMAL", reason="Standard learning question.")
