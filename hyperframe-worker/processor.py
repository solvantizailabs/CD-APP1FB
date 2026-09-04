"""
generate_video(job_id) - Part E/F/G's core worker logic.

Reuses the existing storyboard + TTS + HTML-compile pipeline
(backend.app.services.visual_learning.visual_learning_service.generate_visual_lesson_stream)
completely unchanged, per Part E's "do not rewrite the actual HyperFrame
generation logic unless necessary". That function POSTs its compile step to
HYPERFRAMES_SERVICE_URL (the split done in an earlier session) - this worker's
entrypoint.sh starts that same hyperframes_service on localhost inside the
worker container itself, so the worker is fully self-contained (no network
dependency on the separately-deployed App Platform hyperframes_service) while
still reusing 100% of the real code, zero duplication.

New work in this file is only what didn't exist before: driving that stream to
completion synchronously (instead of forwarding it to a browser via SSE),
then handing off to renderer.py for the actual MP4 render, which is the part
that never existed until this worker.
"""

import asyncio
import json
import logging
import os
import shutil
import time

import database
import renderer
import storage

logger = logging.getLogger(__name__)

HYPERFRAMES_ENGINE_DIR = os.getenv("HYPERFRAMES_ENGINE_DIR", "/app/hyperframes_engine")


async def _run_storyboard_pipeline(payload: dict) -> dict:
    """Drains generate_visual_lesson_stream to its final lesson_ready event.
    Imported lazily so a failure to import the (large) backend dependency
    tree surfaces as a job failure, not a worker crash-loop at startup."""
    from backend.app.services.visual_learning.visual_learning_service import generate_visual_lesson_stream

    stream = generate_visual_lesson_stream(
        query=payload["query"],
        book_uuid=payload.get("book_uuid", ""),
        class_name=payload["class_name"],
        subject=payload.get("subject", "General Knowledge"),
        student_profile={"class": payload["class_name"]},
    )

    lesson_ready_data = None
    async for sse_chunk in stream:
        raw = sse_chunk.strip()
        if raw.startswith("data: "):
            raw = raw[6:]
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if chunk.get("type") == "lesson_ready":
            lesson_ready_data = chunk

    if not lesson_ready_data or not lesson_ready_data.get("lesson_id"):
        raise RuntimeError("Storyboard pipeline finished without a lesson_ready event")
    return lesson_ready_data


def generate_video(job_id: str, worker_id: str) -> None:
    """Synchronous entry point called from worker.py's main loop. Owns the
    full Part F flow: Mark PROCESSING -> Generate HyperFrame -> Create MP4 ->
    Upload -> Update PostgreSQL -> Mark COMPLETED -> Clean temp files."""
    started_at = time.time()
    lesson_id = None
    try:
        database.update_job(job_id, {
            "status": "processing",
            "worker_id": worker_id,
            "started_at": database.now_iso(),
            "last_heartbeat_at": database.now_iso(),
        })

        payload = database.get_job_payload(job_id)
        if not payload:
            raise RuntimeError(f"No job payload found in Redis for job {job_id} (expired or already consumed)")

        lesson_ready = asyncio.run(_run_storyboard_pipeline(payload))
        lesson_id = lesson_ready["lesson_id"]

        database.update_job(job_id, {"last_heartbeat_at": database.now_iso()})

        mp4_path = renderer.render_lesson_to_mp4(lesson_id)

        database.update_job(job_id, {"status": "uploading", "last_heartbeat_at": database.now_iso()})
        video_url = storage.upload_video(mp4_path, job_id)

        database.update_job(job_id, {
            "status": "completed",
            "video_path": video_url,
            "completed_at": database.now_iso(),
        })
        logger.info(f"[processor] Job {job_id} completed in {time.time() - started_at:.1f}s -> {video_url}")

    except Exception as e:
        logger.exception(f"[processor] Job {job_id} failed")
        database.update_job(job_id, {
            "status": "failed",
            "error_message": str(e)[:2000],
            "failed_at": database.now_iso(),
        })
    finally:
        if lesson_id:
            lesson_dir = os.path.join(HYPERFRAMES_ENGINE_DIR, "outputs", lesson_id)
            shutil.rmtree(lesson_dir, ignore_errors=True)
