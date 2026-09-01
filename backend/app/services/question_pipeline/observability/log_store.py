"""
Persists one structured pipeline_logs record per question-pipeline request:
Firestore (source of truth, what the dashboard reads) + a local JSON mirror
under terminal_test/outputs/pipeline_logs/ (same pattern as the rest of
terminal_test/outputs/ elsewhere in this repo, for offline debugging without
a Firestore round trip).

Fail-open, matching every other personalization/history write in this
codebase (_save_history/_save_cache in pipeline.py) - a logging failure
must never break the student-facing answer.
"""
import datetime
import json
import logging
import os
import uuid
from typing import Any, Dict

logger = logging.getLogger(__name__)

_LOCAL_LOG_DIR = os.path.join("terminal_test", "outputs", "pipeline_logs")
FIRESTORE_COLLECTION = "pipeline_logs"


def new_request_id() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]


def save_pipeline_log(record: Dict[str, Any]) -> None:
    _save_local(record)
    _save_firestore(record)


def _save_local(record: Dict[str, Any]) -> None:
    try:
        os.makedirs(_LOCAL_LOG_DIR, exist_ok=True)
        path = os.path.join(_LOCAL_LOG_DIR, f"{record['request_id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, default=str, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[PipelineLog] local JSON write failed: {e}")


def _save_firestore(record: Dict[str, Any]) -> None:
    try:
        from backend.app.core.firebase.firebase_init import db
        db.collection(FIRESTORE_COLLECTION).document(record["request_id"]).set(record)
    except Exception as e:
        logger.warning(f"[PipelineLog] Firestore write failed: {e}")
