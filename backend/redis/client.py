"""
Redis connection for the video job queue (DronaX - DigitalOcean Platform.pdf,
Part D). Separate from backend/app/core/redis_service.py on purpose - that one
is the existing session/cache client (with a silent in-memory fallback, fine to
lose on restart). A job queue must NOT silently fall back to memory: a queued
job that disappears on restart is a lost/stuck video for a student, so this
client fails loudly instead.

Supports REDIS_URL directly (what the doc's Part P env var list uses) and
falls back to the existing REDIS_HOST/REDIS_PORT/REDIS_PASSWORD vars already
used by redis_service.py, so one Redis instance serves both without needing
two different env var sets configured on the same deployment.
"""

import os
import redis as redis_lib

_client = None


def get_redis_client() -> "redis_lib.Redis":
    global _client
    if _client is not None:
        return _client

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        _client = redis_lib.from_url(redis_url, decode_responses=True)
    else:
        _client = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )

    # Fail fast and loud here - unlike redis_service.py, a broken connection to
    # the job queue must not be silently swallowed.
    _client.ping()
    return _client
