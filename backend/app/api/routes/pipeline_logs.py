"""
Read-only API for the Pipeline Trace Inspector dashboard
(public/pipeline-logs.html). Queries the `pipeline_logs` Firestore
collection written by
backend/app/services/question_pipeline/observability/log_store.py (fresh
records) and backend/scripts/migrate_pipeline_logs.py (backfilled legacy
records, flagged `legacy: true`).

Internal/admin surface - not linked from student-facing UI.
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from google.cloud import firestore

from backend.app.core.firebase.firebase_init import db
from backend.app.services.question_pipeline.observability import tracker_session
from backend.app.services.question_pipeline.observability.log_store import FIRESTORE_COLLECTION

logger = logging.getLogger(__name__)
router = APIRouter()

# Lightweight shared-passphrase gate (not Firebase admin auth - decided
# 2026-08-31, this is meant to stay a quick internal viewer, not carry a
# full login flow). Set PIPELINE_LOGS_KEY in .env; the dashboard prompts
# for it once and remembers it in the browser's localStorage. Fails CLOSED
# if the env var is unset, rather than silently leaving the endpoint open.
_PIPELINE_LOGS_KEY = os.getenv("PIPELINE_LOGS_KEY", "")


def verify_pipeline_logs_key(x_pipeline_logs_key: str = Header(default="")):
    if not _PIPELINE_LOGS_KEY:
        raise HTTPException(status_code=503, detail="PIPELINE_LOGS_KEY is not configured on the server.")
    if x_pipeline_logs_key != _PIPELINE_LOGS_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing pipeline logs key.")

_LIST_FIELDS = (
    "request_id", "timestamp", "uid", "tracker_id", "book_session_id",
    "raw_question", "resolved_question", "status", "route", "format_decision",
    "total_duration_ms", "total_tokens", "total_cost", "legacy",
)


def _sort_key(doc_dict: dict):
    """
    Both fresh records (pipeline.py stamps a real UTC datetime) and migrated
    legacy records (copied from the original Firestore SERVER_TIMESTAMP) carry
    a real `timestamp` - sort on that directly rather than comparing
    request_id strings, whose formats differ between the two
    (`YYYYMMDD_HHMMSS_hex` vs `legacy_{uid}_{orig_doc_id}`) and sort wrong
    against each other as plain strings ("legacy_..." > "2026...").
    """
    ts = doc_dict.get("timestamp")
    if ts is None:
        return (0, "")
    if hasattr(ts, "timestamp"):
        return (1, ts.timestamp())
    return (0, doc_dict.get("request_id", ""))


@router.get("/api/admin/pipeline-logs", tags=["Pipeline Trace Inspector"], dependencies=[Depends(verify_pipeline_logs_key)])
async def list_pipeline_logs(
    tracker_id: Optional[str] = Query(None),
    uid: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """List recent pipeline_logs, optionally narrowed to one tracker_id or uid."""
    try:
        query = db.collection(FIRESTORE_COLLECTION)
        if tracker_id:
            query = query.where(filter=firestore.FieldFilter("tracker_id", "==", tracker_id))
        elif uid:
            query = query.where(filter=firestore.FieldFilter("uid", "==", uid))

        docs = [d.to_dict() for d in query.stream()]
        docs.sort(key=_sort_key, reverse=True)
        docs = docs[:limit]

        rows = [{field: d.get(field) for field in _LIST_FIELDS} for d in docs]
        return {"count": len(rows), "requests": rows}
    except Exception as e:
        logger.error(f"[PipelineLogs] list failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/pipeline-logs/{request_id}", tags=["Pipeline Trace Inspector"], dependencies=[Depends(verify_pipeline_logs_key)])
async def get_pipeline_log_detail(request_id: str):
    """Full record for one request - stages, LLM calls, follow-up/chunk-cache detail."""
    try:
        doc = db.collection(FIRESTORE_COLLECTION).document(request_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"No pipeline log found for request_id={request_id}")
        return doc.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PipelineLogs] detail fetch failed for {request_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/pipeline-logs/session/{uid}", tags=["Pipeline Trace Inspector"], dependencies=[Depends(verify_pipeline_logs_key)])
async def get_tracker_session_status(uid: str):
    """Current login-session tracker status for a uid - tracker_id + hours_left
    for the dashboard's countdown display. Does NOT create a new tracker if
    none exists (this is a read for the dashboard, not a pipeline call)."""
    from backend.app.core.redis_service import redis_service
    key = "login_session:" + uid
    existing = redis_service.get_session(key)
    if not existing:
        return {"uid": uid, "tracker_id": None, "hours_left": None, "active": False}
    return {
        "uid": uid,
        "tracker_id": existing.get("tracker_id"),
        "hours_left": tracker_session._hours_left(key),
        "active": True,
    }
