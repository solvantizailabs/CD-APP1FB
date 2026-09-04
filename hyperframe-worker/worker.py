"""
Part F, Step 6 - the worker's main loop:
START -> Connect Redis -> Connect Supabase -> Wait for job -> Get job ->
Mark PROCESSING -> Generate HyperFrame -> Create MP4 -> Upload -> Update DB ->
Mark COMPLETED -> Clean temp files -> Get next job.

"Connect Supabase" has no separate persistent connection step here since
database.py talks to it over plain HTTPS (PostgREST) per-request rather than
a stateful connection - there is nothing to hold open between jobs.
"""

import logging
import os
import socket
import time

from redis_client import get_redis_client
import processor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")

QUEUE_KEY = "hyperframe:video_jobs:queue"
INFLIGHT_KEY = "hyperframe:video_jobs:inflight"
WORKER_ID = os.getenv("WORKER_ID") or socket.gethostname()
POLL_TIMEOUT_S = 5


def main():
    logger.info(f"[worker] Starting HyperFrame worker, worker_id={WORKER_ID}")

    r = get_redis_client()
    logger.info("[worker] Connected to Redis")

    # The real API calls this once in its FastAPI lifespan startup handler
    # (backend/app/main.py:89) - this worker never runs that lifecycle, so
    # without calling it here too, qdrant_service.openai_client stays None
    # and every job fails with "OpenAI Client is not initialized in
    # qdrant_service" (confirmed by a real failed job before this fix).
    logger.info("[worker] Initializing Qdrant/OpenAI client...")
    try:
        from backend.app.services.retrieval import qdrant_service
        qdrant_service.initialize()
        logger.info("[worker] Qdrant/OpenAI client ready.")
    except Exception as e:
        logger.error(f"[worker] Qdrant initialization failed: {e}. Jobs will fail until this is resolved.")

    logger.info("[worker] Ready. Waiting for jobs...")
    while True:
        try:
            result = r.brpop(QUEUE_KEY, timeout=POLL_TIMEOUT_S)
        except Exception as e:
            logger.error(f"[worker] Redis error while waiting for job: {e}. Retrying in 5s.")
            time.sleep(5)
            continue

        if result is None:
            continue  # Timed out with no job - loop back and wait again.

        _, job_id = result
        r.sadd(INFLIGHT_KEY, job_id)
        logger.info(f"[worker] Picked up job {job_id}")
        try:
            processor.generate_video(job_id, WORKER_ID)
        finally:
            r.srem(INFLIGHT_KEY, job_id)
        logger.info(f"[worker] Finished job {job_id}, getting next job...")


if __name__ == "__main__":
    main()
