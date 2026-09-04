"""
video_jobs tracking, on Firestore instead of a new Postgres table - this app's
actual database everywhere else, and unlike Postgres it needs no manual
schema/DDL step: a document is created the moment the app first writes one.

Collection: video_jobs
Document ID: the job id (Firestore auto-ID, generated on create)
Fields: student_id, question_id, session_id, status, video_path,
        error_message, retry_count, worker_id, last_heartbeat_at,
        created_at, started_at, completed_at, failed_at
(same field list as the doc's Part C, Step 3 - just a Firestore document
instead of a SQL row)
"""

from datetime import datetime, timezone

from backend.app.core.firebase.firebase_init import db

COLLECTION = "video_jobs"


def create_job(data: dict) -> str:
    doc_ref = db.collection(COLLECTION).document()
    doc_ref.set({
        **data,
        "status": data.get("status", "queued"),
        "retry_count": 0,
        "created_at": datetime.now(timezone.utc),
    })
    return doc_ref.id


def update_job(job_id: str, data: dict) -> None:
    db.collection(COLLECTION).document(job_id).set(data, merge=True)


def get_job(job_id: str) -> dict | None:
    snap = db.collection(COLLECTION).document(job_id).get()
    if not snap.exists:
        return None
    return {"id": snap.id, **snap.to_dict()}


def get_stale_processing_jobs(stale_seconds: int) -> list:
    """Part Z Test 4: jobs stuck in 'processing' whose worker heartbeat has
    gone quiet longer than `stale_seconds` - used by the Controller to detect
    a dead worker and requeue its job."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    # Firestore compound queries (equality + range filter on different
    # fields) need a composite index. The FIRST time this runs, Firestore
    # will raise an error containing a direct "create this index" link -
    # click it once and the query works from then on. Expected one-time
    # friction, not a bug.
    query = (
        db.collection(COLLECTION)
        .where("status", "==", "processing")
        .where("last_heartbeat_at", "<", cutoff)
    )
    return [{"id": d.id, **d.to_dict()} for d in query.stream()]
