"""
Login-session tracker: one tracker_id per (uid, FIXED 24h window),
independent of the existing book/topic session in
backend/app/services/chat/session_service.py (that one is keyed by
book_uuid and is a different concept - see
project memory "project_question_understanding_routing_phase" /
2026-08-31 planning conversation for why the two are kept separate).

There is no single "login" event to hang this on in this backend -
auth_middleware.py verifies a Firebase token per-request, not once at
login - so the tracker is created lazily on the first pipeline call for a
uid, and reused for every call after that until it expires.

Decision (2026-08-31): FIXED window, not rolling/idle-timeout. The TTL is
set once at creation and never refreshed on later reads, so a tracker_id
expires exactly 24h after the student's first question in that window,
no matter how much they keep asking. This is why _hours_left reads the
key's real remaining Redis TTL rather than recomputing anything.
"""
import time
import uuid
from typing import Dict, Optional

from backend.app.core.redis_service import redis_service

_TTL_SECONDS = 86400
_KEY_PREFIX = "login_session:"


def get_or_create_tracker(uid: str) -> Optional[Dict]:
    """
    Returns {"tracker_id", "uid", "created_at", "hours_left"} for this uid,
    or None if uid is empty - an unauthenticated request
    (auth_middleware.py explicitly allows these through for backward
    compatibility) has no student identity to bucket a tracker under.
    """
    if not uid:
        return None

    key = _KEY_PREFIX + uid
    existing = redis_service.get_session(key)
    if existing:
        return {**existing, "hours_left": _hours_left(key)}

    record = {
        "tracker_id": "trk_" + uuid.uuid4().hex[:12],
        "uid": uid,
        "created_at": time.time(),
    }
    redis_service.save_session(key, record, ttl=_TTL_SECONDS)
    return {**record, "hours_left": round(_TTL_SECONDS / 3600, 2)}


def _hours_left(key: str) -> float:
    try:
        ttl_seconds = redis_service.r.ttl(key)
        if ttl_seconds is None or ttl_seconds < 0:
            return 0.0
        return round(ttl_seconds / 3600, 2)
    except Exception:
        return 0.0
