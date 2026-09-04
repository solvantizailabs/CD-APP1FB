"""
Part U/V/X/Y/Z main loop: continuously checks queue depth, reconciles the
worker fleet (create/destroy) via digitalocean.py's pluggable backend, and
detects/requeues jobs from dead workers (Part Z Test 4).

Decisions made here that the doc left as open blanks (flagged as open
decisions before any of this was built):
- IDLE_GRACE_SECONDS (default 120s): how long queue depth must stay at 0
  before destroying the last worker(s), to avoid create/destroy thrashing.
- HEARTBEAT_STALE_SECONDS (default 180s): a 'processing' job whose
  last_heartbeat_at is older than this is assumed to belong to a dead worker
  and gets requeued.
- POLL_INTERVAL_SECONDS (default 10s): how often the reconcile loop runs.
All three are env-var overridable, not hardcoded, so they can be tuned from
real load-test results per Part O without a code change.
"""

import logging
import os
import time

from backend.app.core.video_jobs_firestore import get_stale_processing_jobs

from redis_client import get_redis_client, queue_depth
from scaling import desired_worker_count
from digitalocean import get_fleet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("controller")

POLL_INTERVAL_SECONDS = int(os.getenv("CONTROLLER_POLL_INTERVAL_S", "10"))
IDLE_GRACE_SECONDS = int(os.getenv("CONTROLLER_IDLE_GRACE_S", "120"))
HEARTBEAT_STALE_SECONDS = int(os.getenv("CONTROLLER_HEARTBEAT_STALE_S", "180"))
# Independent safety net (Query 5 from the earlier open-decisions list): kills
# any worker regardless of Controller state once it's been alive this long,
# so a Controller crash/restart can't leave an orphaned Droplet billing
# forever. Generous vs. the observed multi-minute render time.
MAX_WORKER_LIFETIME_SECONDS = int(os.getenv("CONTROLLER_MAX_WORKER_LIFETIME_S", str(60 * 60)))


_stale_check_broken_logged = False


def _requeue_stale_jobs(r):
    """Part Z Test 4: job=processing, worker dies, heartbeat disappears,
    Controller detects it, job gets requeued for another worker. Reads via
    Firestore (backend/app/core/video_jobs_firestore.py) - the app's real
    database - not Postgres.

    This query needs a one-time Firestore composite index (equality +
    range filter on different fields). Until that index exists, Firestore
    rejects every call identically - without the guard below, that means
    the exact same error logged every POLL_INTERVAL_SECONDS forever, which
    is indistinguishable from a genuine crash-loop in the logs. Log it
    loudly ONCE with the actual fix, then go quiet (still retrying, just
    not spamming) until it starts working.
    """
    global _stale_check_broken_logged
    try:
        from backend.app.core.video_jobs_firestore import update_job
        for job in get_stale_processing_jobs(HEARTBEAT_STALE_SECONDS):
            job_id = job["id"]
            logger.warning(f"[controller] Job {job_id} heartbeat stale - requeuing")
            update_job(job_id, {
                "status": "queued",
                "retry_count": (job.get("retry_count") or 0) + 1,
            })
            r.lpush("hyperframe:video_jobs:queue", job_id)
        _stale_check_broken_logged = False
    except Exception as e:
        if not _stale_check_broken_logged:
            logger.error(
                "[controller] Stale-job check failed - most likely cause: Firestore "
                "needs a one-time composite index for this query. The full error below "
                "usually contains a direct 'create this index' link - open it, click "
                "Create, wait ~1-2 minutes, and this will start working without a "
                "restart. Suppressing repeats of this exact error until it's resolved.\n"
                f"{e}"
            )
            _stale_check_broken_logged = True


def main():
    r = get_redis_client()
    fleet = get_fleet()
    logger.info(f"[controller] Started. mode={os.getenv('CONTROLLER_MODE', 'local')} "
                f"poll={POLL_INTERVAL_SECONDS}s idle_grace={IDLE_GRACE_SECONDS}s")

    idle_since = None
    worker_created_at = {}
    last_error_signature = None
    consecutive_same_error = 0

    while True:
        try:
            depth = queue_depth(r)
            target = desired_worker_count(depth)
            workers = fleet.list_workers()
            current = len(workers)

            now = time.time()

            # Independent max-lifetime kill switch, checked every loop
            # regardless of queue depth.
            for w in workers:
                wid = getattr(w, "id", None) or w.get("id")
                created = worker_created_at.setdefault(wid, now)
                if now - created > MAX_WORKER_LIFETIME_SECONDS:
                    logger.warning(f"[controller] Worker {wid} exceeded max lifetime - destroying")
                    fleet.destroy_worker(wid)
                    worker_created_at.pop(wid, None)

            workers = fleet.list_workers()
            current = len(workers)

            if depth > 0:
                idle_since = None
            elif idle_since is None:
                idle_since = now

            if target > current:
                for _ in range(target - current):
                    new_id = fleet.create_worker(env={
                        "REDIS_URL": os.getenv("REDIS_URL", ""),
                        "REDIS_HOST": os.getenv("REDIS_HOST", ""),
                        "REDIS_PORT": os.getenv("REDIS_PORT", ""),
                        "REDIS_PASSWORD": os.getenv("REDIS_PASSWORD", ""),
                        "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),  # MP4 -> Supabase Storage (unrelated to job tracking)
                        "SUPABASE_KEY": os.getenv("SUPABASE_KEY", ""),
                        "FIREBASE_SERVICE_ACCOUNT_JSON": os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", ""),  # video_jobs Firestore access
                        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
                        "QDRANT_URL": os.getenv("QDRANT_URL", ""),
                        "QDRANT_API_KEY": os.getenv("QDRANT_API_KEY", ""),
                    })
                    worker_created_at[new_id] = now
                    logger.info(f"[controller] Scaled up: created worker {new_id} (queue depth={depth})")
            elif target < current and (target > 0 or (idle_since and now - idle_since > IDLE_GRACE_SECONDS)):
                for w in workers[: current - target]:
                    wid = getattr(w, "id", None) or w.get("id")
                    fleet.destroy_worker(wid)
                    worker_created_at.pop(wid, None)
                    logger.info(f"[controller] Scaled down: destroyed worker {wid} (queue depth={depth})")

            _requeue_stale_jobs(r)
            last_error_signature = None
            consecutive_same_error = 0

        except Exception as e:
            signature = f"{type(e).__name__}: {e}"
            if signature == last_error_signature:
                # Same failure as last cycle - this is a persistent problem
                # (bad config, unreachable Docker socket, missing env var),
                # not a transient blip. Printing a full traceback every
                # POLL_INTERVAL_SECONDS forever is exactly what makes a real
                # bug look like "hundreds of identical log lines" and
                # obscures the one traceback that actually mattered - so log
                # only a one-line heartbeat every 6th repeat (~1 min at the
                # default 10s poll) instead.
                consecutive_same_error += 1
                if consecutive_same_error % 6 == 1:
                    logger.error(f"[controller] Reconcile loop still failing ({consecutive_same_error}x): {signature}")
            else:
                logger.exception("[controller] Reconcile loop error (new)")
                last_error_signature = signature
                consecutive_same_error = 1

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
