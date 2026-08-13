import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.app.core import subject_config
from backend.app.core.firebase.firebase_init import db
from backend.app.core import firestore_service
from backend.app.core.subject_classifier import (
    build_chapter_subject_map,
    clean_class_id,
    get_class_subject_docs,
    resolve_subject,
)
from backend.app.services.chat.session_service import session_manager

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_HISTORY_LIMIT = 50


def _get_available_subjects(class_name: Optional[str]) -> list:
    class_num = int(clean_class_id(class_name))
    configured_subjects = subject_config.get_subjects_for_class(class_num)

    subject_docs = get_class_subject_docs(class_name)
    registered_subjects = {s.id.lower() for s in subject_docs}

    if not registered_subjects:
        return configured_subjects
    return [s for s in configured_subjects if s.lower() in registered_subjects] or list(registered_subjects)


@router.get("/api/history", tags=["History"])
async def get_history(
    uid: str = Query(...),
    class_name: Optional[str] = Query(None),
    limit: int = Query(DEFAULT_HISTORY_LIMIT, ge=1, le=200),
):
    try:
        available_subjects = _get_available_subjects(class_name)
        chapter_subject_map = build_chapter_subject_map(class_name)
        valid_subjects = set(available_subjects)

        docs = (
            db.collection("users")
            .document(uid)
            .collection("queries")
            .order_by("timestamp", direction="DESCENDING")
            .limit(limit)
            .stream()
        )

        buckets = {s: [] for s in available_subjects}
        buckets["uncategorized"] = []

        for d in docs:
            data = d.to_dict()
            resolved = resolve_subject(
                class_name,
                data.get("chapter_name"),
                data.get("query"),
                data.get("subject"),
                valid_subjects=valid_subjects,
                chapter_subject_map=chapter_subject_map,
            )
            bucket_key = resolved if resolved in buckets else "uncategorized"

            timestamp = data.get("timestamp")
            buckets[bucket_key].append({
                "doc_id": d.id,
                "query": data.get("query"),
                "chapter_name": data.get("chapter_name") or "Unknown",
                "has_video": bool(data.get("video_url")),
                "video_url": data.get("video_url"),
                "llm_response": data.get("llm_response"),
                "has_audio": bool(data.get("audio_url")),
                "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
            })

        groups = [
            {"subject": subject, "items": items}
            for subject, items in buckets.items()
            if subject != "uncategorized" and items
        ]
        if buckets["uncategorized"]:
            groups.append({"subject": "uncategorized", "items": buckets["uncategorized"]})

        return {
            "subjects_available": available_subjects,
            "groups": groups,
        }
    except Exception as e:
        logger.error(f"Failed to get history for uid={uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class ReplayRequest(BaseModel):
    uid: str
    doc_id: str


@router.post("/api/history/replay", tags=["History"])
async def replay_history_item(payload: ReplayRequest):
    try:
        doc_ref = db.collection("users").document(payload.uid).collection("queries").document(payload.doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="History item not found")

        data = doc.to_dict()
        subject = (data.get("subject") or "").strip().lower()
        class_name = data.get("class")

        book_uuid = None
        if subject and subject != "all":
            summary_doc = firestore_service.load_summary_from_firestore(class_name, subject)
            if summary_doc:
                book_uuid = summary_doc.get("book_uuid")
        if not book_uuid:
            book_uuid = f"replay_{class_name}_{subject or 'uncategorized'}"

        session = session_manager.get_or_create_session(book_uuid, None)
        session_manager.add_turn(session["session_id"], {
            "query": data.get("query"),
            "answer": data.get("llm_response"),
            "intent_type": "USE_CACHED_CONTEXT",
            "reformulated": data.get("reformulated_query"),
            "follow_ups": [],
            "is_basic_question": False,
            "is_same_topic_as_streak": False,
        })

        retrieved_sources = data.get("retrieved_sources")
        if retrieved_sources:
            session_manager.update_topic_chunks(session["session_id"], retrieved_sources)

        return {
            "session_id": session["session_id"],
            "book_uuid": book_uuid,
            "query": data.get("query"),
            "llm_response": data.get("llm_response"),
            "video_url": data.get("video_url"),
            "audio_url": data.get("audio_url"),
            "storyboard_data": data.get("storyboard_data"),
            "subject": subject,
            "chapter_name": data.get("chapter_name"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to replay history item {payload.doc_id} for uid={payload.uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
