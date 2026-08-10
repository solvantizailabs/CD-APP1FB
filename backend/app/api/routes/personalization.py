"""
Daily profile refresh (personalized_learning.md SS "daily refresh" discussion).

The skill x engagement quadrant already recomputes LIVE on every turn
(profile_service.compute_quadrant, called from chat.py) - that's more
current than any daily batch could be. What a nightly job adds on top is a
DATED SNAPSHOT of that state, so profile evolution is inspectable day over
day, and so there's a concrete integration point for an external scheduler.

This endpoint does not itself run on a timer - nothing in this codebase can
guarantee wall-clock execution at a fixed IST time. Wiring an actual
"12:00 AM IST daily" trigger means pointing a Google Cloud Scheduler job (or
equivalent) at POST /api/personalization/daily-refresh once this backend is
deployed - that's an infra/ops step outside this repo, not a code change.
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Query

from backend.app.core.firebase.firebase_init import db
from backend.app.services.personalization import profile_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _snapshot_uid(uid: str) -> dict:
    """Recompute the quadrant one more time (cheap, idempotent) and write a
    dated snapshot under users/{uid}/daily_digests/{date}."""
    quadrant_result = profile_service.compute_quadrant(uid)
    profile_ctx = profile_service.get_profile_context(uid)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        "date": date_str,
        "quadrant": profile_ctx.get("quadrant"),
        "skill": profile_ctx.get("skill"),
        "engagement": profile_ctx.get("engagement"),
        "response_style": profile_ctx.get("response_style"),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("users").document(uid).collection("daily_digests").document(date_str).set(snapshot, merge=True)
    return snapshot


@router.post("/api/personalization/daily-refresh", tags=["Personalization"])
async def daily_refresh(uid: str = Query(None, description="Refresh a single student; omit to refresh all students (bounded batch)"),
                         limit: int = Query(200, description="Max students to process when uid is omitted")):
    """
    Recompute + snapshot the skill x engagement quadrant for one student, or
    a bounded batch of students. Intended to be called by an external daily
    scheduler (see module docstring) - safe to call more often too, since
    compute_quadrant is idempotent.
    """
    if uid:
        snapshot = _snapshot_uid(uid)
        return {"processed": 1, "results": {uid: snapshot}}

    results = {}
    docs = db.collection("users").where("role", "==", "student").limit(limit).stream()
    for doc in docs:
        try:
            results[doc.id] = _snapshot_uid(doc.id)
        except Exception as e:
            logger.error(f"[DAILY_REFRESH] Failed for uid={doc.id}: {e}")
    return {"processed": len(results), "results": results}
