"""
video_jobs tracking for the worker - Firestore, not Postgres (matches the
app's real database; see backend/app/core/video_jobs_firestore.py for why).
Reuses the existing Firebase Admin init (backend/app/core/firebase/
firebase_init.py) unchanged rather than re-implementing credential loading -
that file already handles both a service-account JSON file and the
FIREBASE_SERVICE_ACCOUNT_JSON/FIREBASE_CREDENTIALS env vars, so this worker
just needs whichever of those two is available.
"""

import logging
from datetime import datetime, timezone

from backend.app.core.firebase.firebase_init import db
from redis_client import get_redis_client
import json

logger = logging.getLogger(__name__)

COLLECTION = "video_jobs"


def update_job(job_id: str, data: dict) -> None:
    try:
        db.collection(COLLECTION).document(job_id).set(data, merge=True)
    except Exception as e:
        logger.error(f"[database] update_job({job_id}) failed: {e}")


def get_job_payload(job_id: str) -> dict:
    """Retrieves and deletes the generation payload the API stashed in Redis
    at /api/video/generate time (see backend/app/api/routes/video.py) - one
    job, one read, matches the 'get job' step in Part F."""
    r = get_redis_client()
    key = f"hyperframe:job_payload:{job_id}"
    raw = r.get(key)
    if raw is None:
        return {}
    r.delete(key)
    return json.loads(raw)


def now_iso():
    return datetime.now(timezone.utc)
