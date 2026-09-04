"""
Async video generation API (DronaX - DigitalOcean Platform.pdf, Parts C, D, H).

Deliberately a NEW, separate endpoint pair rather than a rewrite of chat.py's
existing VIDEO_REQUIRED streaming flow (see backend/app/api/routes/chat.py
~line 684). That flow already streams narrated text + audio scene-by-scene
while HyperFrame's fast HTML compile runs in the background - it works today
and rewriting it into "return instantly, poll a job_id" would be a real UX
regression (no more live narration, just a spinner). This module instead
covers the doc's literal ask: a real MP4 render, which is genuinely slow
(multi-minute, confirmed by hand against the actual `hyperframes` CLI) and so
genuinely needs the queue/worker split the doc describes. The two systems
produce two different artifacts (interactive HTML lesson vs downloadable MP4)
and can coexist.

Job tracking uses Firestore (video_jobs_firestore.py), not a new Postgres
table - matches the app's actual database everywhere else, and needs no
manual schema step (Firestore creates a document on first write). The MP4
file itself still goes to Supabase Storage (hyperframe-worker/storage.py) -
that's unrelated object/blob storage, not this decision.

The generation payload (query text, class, subject, book_uuid) doesn't fit
the doc's Part C field list, so it's stashed in Redis under a per-job key
with a TTL, set here at enqueue time and consumed once by the worker - see
hyperframe-worker/database.py's get_job_payload.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.core import video_jobs_firestore
from backend.redis.queue import enqueue_job
from backend.redis.client import get_redis_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video", tags=["video"])

JOB_PAYLOAD_TTL_SECONDS = 6 * 60 * 60  # 6h - generous vs. any realistic queue backlog


class VideoGenerateRequest(BaseModel):
    query: str
    class_name: str
    subject: str = "General Knowledge"
    book_uuid: str = ""
    student_id: str = ""
    session_id: str = ""
    question_id: str = ""


@router.post("/generate")
async def generate_video(req: VideoGenerateRequest):
    """Create a video_jobs Firestore doc, stash its generation payload in
    Redis, push the job id onto the queue, and return immediately - Part D's
    'Create video_jobs record -> Put job ID into Redis -> Return response'."""
    try:
        job_id = video_jobs_firestore.create_job({
            "student_id": req.student_id or None,
            "question_id": req.question_id or None,
            "session_id": req.session_id or None,
        })
    except Exception as e:
        logger.error(f"[video.generate] Failed to create video_jobs doc: {e}")
        raise HTTPException(status_code=503, detail="Could not create video job (Firestore unavailable)")

    payload = {
        "query": req.query,
        "class_name": req.class_name,
        "subject": req.subject,
        "book_uuid": req.book_uuid,
        "student_id": req.student_id,
    }
    try:
        r = get_redis_client()
        r.setex(f"hyperframe:job_payload:{job_id}", JOB_PAYLOAD_TTL_SECONDS, json.dumps(payload))
        enqueue_job(job_id)
    except Exception as e:
        logger.error(f"[video.generate] Failed to enqueue job {job_id}: {e}")
        video_jobs_firestore.update_job(job_id, {
            "status": "failed",
            "error_message": f"enqueue_failed: {e}",
        })
        raise HTTPException(status_code=503, detail="Could not enqueue video job (Redis unavailable)")

    return {
        "video_job_id": job_id,
        "video_status": "queued",
    }


@router.get("/{job_id}/status")
async def get_video_status(job_id: str):
    """Part H, Step 8. Matches the doc's exact response shape."""
    job = video_jobs_firestore.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    response = {
        "job_id": job_id,
        "status": job.get("status"),
    }
    if job.get("status") == "completed" and job.get("video_path"):
        response["video_url"] = job["video_path"]
    if job.get("status") == "failed" and job.get("error_message"):
        response["error_message"] = job["error_message"]
    return response
