"""
Per-student profile, preference, and skill x engagement quadrant helpers.

All reads/writes target the nested `users/{uid}` document only, per the
approved schema decision (personalized_learning.md SS6.6) - never the old
flat collections (user_stats, student_mistakes, etc.).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from backend.app.core.firebase.firebase_init import db

logger = logging.getLogger(__name__)

RESPONSE_STYLES = ("storytelling", "direct", "detailed")

# Simple v1 heuristic for "does this question reach above a plain
# recall-level question" (SS6.2's skill signal, SS4's research note that this
# is rules-based, not an ML model, for the first build). Deliberately crude -
# upgradeable once real quiz-performance data exists.
_ADVANCED_MARKERS = (
    "why", "derive", "prove", "explain the reason", "compare", "difference between",
    "relationship between", "analyze", "analyse", "what if", "how does", "justify",
)


def _clean_uid(uid: Optional[str]) -> Optional[str]:
    if not uid or uid == "anonymous":
        return None
    return uid


def set_preferences(
    uid: str,
    response_style: Optional[str] = None,
    tough_subjects: Optional[List[str]] = None,
    easy_subjects: Optional[List[str]] = None,
) -> None:
    """Registration-time preferences (SS6.1). Manually seeded for now (SS2.6 -
    no real registration UI exists yet)."""
    uid = _clean_uid(uid)
    if not uid:
        return
    prefs: Dict[str, Any] = {}
    if response_style:
        if response_style not in RESPONSE_STYLES:
            logger.warning(f"[PROFILE] Unknown response_style '{response_style}', ignoring")
        else:
            prefs["response_style"] = response_style
    if tough_subjects is not None:
        prefs["tough_subjects"] = tough_subjects
    if easy_subjects is not None:
        prefs["easy_subjects"] = easy_subjects
    if not prefs:
        return
    try:
        db.collection("users").document(uid).set({"preferences": prefs}, merge=True)
        logger.info(f"[PROFILE] Preferences updated for {uid}: {prefs}")
    except Exception as e:
        logger.error(f"[PROFILE] Failed to save preferences for {uid}: {e}")


def get_profile_context(uid: str) -> Dict[str, Any]:
    """
    Everything the orchestrator needs to know about a student beyond
    name/class/board: response_style preference and current quadrant.
    Returns safe defaults (all None/empty) if the student has no profile yet.
    """
    default = {
        "response_style": None,
        "tough_subjects": [],
        "easy_subjects": [],
        "quadrant": None,
        "skill": None,
        "engagement": None,
    }
    uid = _clean_uid(uid)
    if not uid:
        return default
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            return default
        data = doc.to_dict() or {}
        prefs = data.get("preferences", {}) or {}
        profile = data.get("profile", {}) or {}
        return {
            "response_style": prefs.get("response_style"),
            "tough_subjects": prefs.get("tough_subjects", []),
            "easy_subjects": prefs.get("easy_subjects", []),
            "quadrant": profile.get("quadrant"),
            "skill": profile.get("skill"),
            "engagement": profile.get("engagement"),
        }
    except Exception as e:
        logger.error(f"[PROFILE] Failed to read profile context for {uid}: {e}")
        return default


def is_advanced_question(query: str, grade: int) -> bool:
    """
    FALLBACK ONLY. The real signal is `grade_relative_difficulty` from the
    orchestrator's own JSON output (master_orchestrator_prompt.txt SECTION 3,
    directive 7), which judges a question against actual CURRICULUM_DATA for
    the student's grade - not keywords. This heuristic only fires when that
    field is missing (e.g. a safety-refused UNAUTHORIZED turn that never
    reached generation, or a cache entry written before this field existed).
    """
    q = (query or "").lower()
    marker_hit = any(m in q for m in _ADVANCED_MARKERS)
    long_question = len(q.split()) >= 14
    return marker_hit or long_question


def is_basic_question(query: str) -> bool:
    """Companion heuristic - short, single-fact 'what is X' style questions.
    Used for the repeat-question escalation trigger (SS6.3)."""
    q = (query or "").lower().strip()
    if not q:
        return False
    short = len(q.split()) <= 6
    starts_basic = q.startswith(("what is", "what are", "who is", "define", "meaning of"))
    return short or starts_basic


def record_turn_signals(uid: str, grade: int, query: str,
                         grade_relative_difficulty: Optional[str] = None) -> None:
    """
    Updates the running counters used to compute the skill x engagement
    quadrant (SS6.2). Called once per turn from chat.py.

    `grade_relative_difficulty` should be the orchestrator's own real,
    curriculum-grounded judgment ("below_grade"/"at_grade"/"above_grade" -
    see master_orchestrator_prompt.txt SECTION 3 directive 7). Only falls
    back to the crude keyword/length heuristic (`is_advanced_question`) when
    that field wasn't available for this turn.

    Uses Firestore Increment so concurrent turns can't race/clobber each other.
    """
    uid = _clean_uid(uid)
    if not uid:
        return
    if grade_relative_difficulty == "above_grade":
        advanced = True
    elif grade_relative_difficulty in ("at_grade", "below_grade"):
        advanced = False
    else:
        advanced = is_advanced_question(query, grade)
    try:
        updates = {
            "signals": {
                "total_questions": firestore.Increment(1),
                "advanced_questions": firestore.Increment(1 if advanced else 0),
            },
            "signals_last_active_at": datetime.now(timezone.utc).isoformat(),
        }
        db.collection("users").document(uid).set(updates, merge=True)
    except Exception as e:
        logger.error(f"[PROFILE] Failed to record turn signals for {uid}: {e}")


def record_feedback_signal(uid: str, is_positive: bool) -> None:
    """
    SS2.1 explicitly lists feedback (thumbs up/down + reason on dislike) as
    one of the signals the dynamically-inferred profile should be built
    from - this was infrastructure that existed (the feedback endpoint) but
    was never actually wired into the quadrant. Fixed: called from
    POST /api/feedback in chat.py.
    """
    uid = _clean_uid(uid)
    if not uid:
        return
    try:
        db.collection("users").document(uid).set({"signals": {
            ("positive_feedback_count" if is_positive else "negative_feedback_count"): firestore.Increment(1)
        }}, merge=True)
    except Exception as e:
        logger.error(f"[PROFILE] Failed to record feedback signal for {uid}: {e}")


def compute_quadrant(uid: str) -> Optional[Dict[str, str]]:
    """
    Rules-based skill x engagement classification (SS6.2, mapped from the
    'Skill-Will Matrix' research finding in SS4). Deliberately simple
    thresholds for a first build - no ML model.
    """
    uid = _clean_uid(uid)
    if not uid:
        return None
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        signals = data.get("signals", {}) or {}
        total = signals.get("total_questions", 0) or 0
        advanced = signals.get("advanced_questions", 0) or 0

        if total == 0:
            return None

        advanced_ratio = advanced / total
        skill = "high" if advanced_ratio >= 0.3 else "low"

        # Engagement proxy: total question volume, adjusted by feedback
        # (SS2.1 explicitly lists feedback as an engagement/attitude signal -
        # a student who keeps asking questions but consistently dislikes the
        # answers is not genuinely "engaged" in the CEO's Skill-Will sense,
        # they're frustrated). A real follow-up-depth/session-frequency
        # signal is still future work - documented simplification, not a
        # hidden assumption.
        positive_fb = signals.get("positive_feedback_count", 0) or 0
        negative_fb = signals.get("negative_feedback_count", 0) or 0
        total_fb = positive_fb + negative_fb
        volume_engaged = total >= 5
        feedback_disengaged = total_fb >= 3 and (negative_fb / total_fb) >= 0.5
        engagement = "high" if (volume_engaged and not feedback_disengaged) else "low"

        quadrant = f"{skill}_skill_{engagement}_engagement"
        result = {"skill": skill, "engagement": engagement, "quadrant": quadrant}
        db.collection("users").document(uid).set(
            {"profile": {**result, "updated_at": datetime.now(timezone.utc).isoformat()}},
            merge=True,
        )
        return result
    except Exception as e:
        logger.error(f"[PROFILE] Failed to compute quadrant for {uid}: {e}")
        return None
