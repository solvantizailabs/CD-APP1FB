"""
Stage 5's "new or followup" branch point. Narrowed 2026-08-30 (Decision 6,
docs/ORCHESTRATOR_FRD_V3_ANALYSIS.md): this pipeline handles Direct Question
traffic only, per the team's FRD v3 explicit scope - assessment/calculation/
image questions reach students through their own separate entry points
elsewhere in the app, never through free-text detection here. The earlier
ASSESSMENT/CALCULATION/IMAGE/UNSUPPORTED-as-a-route logic (built from an
earlier, broader spec) is removed - UNSUPPORTED/AMBIGUOUS/INVALID are still
real outcomes, but they're validity-driven pipeline exits handled directly
from ValidationResult (see pipeline.py), not routing decisions.

Deterministic, no LLM call - routing is derived from Stage 2's already-
computed continuity, not a fresh judgment.
"""
import re

from backend.app.services.question_pipeline.schemas import IntentResult, RoutingDecision, ValidationResult


def route_question(validation: ValidationResult, intent: IntentResult) -> RoutingDecision:
    if validation.classification == "FOLLOW_UP":
        return RoutingDecision(route="FOLLOW_UP", reason="Depends on prior conversation turn.")
    return RoutingDecision(route="NORMAL", reason="Standard new question.")


# Stage 5c (non-curriculum path): decide direct-answer vs. live web search.
# Ported unchanged from test_runner.py's _GK_KEYWORDS/is_gk_query gate -
# already deterministic, not LLM-judged, exactly the pattern the team's own
# design principles ask for elsewhere too.
_GK_KEYWORDS = {
    "yesterday", "today", "latest", "recent", "breaking", "live", "ongoing",
    "won", "win", "lost", "score", "result", "match", "election", "elected",
    "protest", "strike", "rally", "arrested", "verdict", "announced", "launched",
    "world cup", "ipl", "fifa", "olympics", "championship",
    "party", "government", "minister", "president", "prime minister",
    "news", "happened", "incident", "2026", "2025",
}


def should_use_web_search(question: str) -> bool:
    """Whole-word match only - a substring check would misfire on e.g.
    "winter" matching the "win" keyword."""
    query_lower = (question or "").lower()
    return any(
        re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", query_lower)
        for kw in _GK_KEYWORDS
    )
