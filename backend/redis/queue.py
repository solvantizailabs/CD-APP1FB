"""
Video job queue on top of Redis (DronaX - DigitalOcean Platform.pdf, Part D).

Uses a plain Redis LIST as the queue (LPUSH producer / BRPOP consumer) rather
than Redis Streams or a pub/sub channel - it's the simplest structure that
gives FIFO ordering and a blocking pop for the worker, and matches the doc's
"Put job ID into Redis" / "Wait for job" / "Get job" language directly. A
separate Redis SET tracks job IDs currently "in flight" (picked up by a
worker but not yet completed) purely so the Controller (Part V) can compute
queue depth as "waiting + in-flight" without also querying Supabase.
"""

from backend.redis.client import get_redis_client

QUEUE_KEY = "hyperframe:video_jobs:queue"
INFLIGHT_KEY = "hyperframe:video_jobs:inflight"


def enqueue_job(job_id: str) -> None:
    r = get_redis_client()
    r.lpush(QUEUE_KEY, job_id)


def dequeue_job(timeout_seconds: int = 5) -> str | None:
    """Blocking pop from the tail (FIFO with lpush). Returns None on timeout
    so the worker's loop can wake up periodically (e.g. to check for a
    shutdown signal) instead of blocking forever."""
    r = get_redis_client()
    result = r.brpop(QUEUE_KEY, timeout=timeout_seconds)
    if result is None:
        return None
    _, job_id = result
    r.sadd(INFLIGHT_KEY, job_id)
    return job_id


def mark_job_done(job_id: str) -> None:
    r = get_redis_client()
    r.srem(INFLIGHT_KEY, job_id)


def queue_depth() -> int:
    """Waiting + in-flight, i.e. work the Controller (Part V) still needs
    capacity for - a job popped by a worker but not yet finished still counts
    as demand, not zero."""
    r = get_redis_client()
    return r.llen(QUEUE_KEY) + r.scard(INFLIGHT_KEY)
