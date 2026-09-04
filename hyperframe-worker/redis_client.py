"""Standalone Redis client for the worker process - intentionally NOT importing
backend.redis.client, since hyperframe-worker/ is meant to run as its own
Docker image/Droplet without needing the full backend/ tree available (Part E:
this is a separate application). Same connection logic, duplicated on purpose
to keep the two deployables independent."""

import os
import redis

_client = None


def get_redis_client():
    global _client
    if _client is not None:
        return _client
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        _client = redis.from_url(redis_url, decode_responses=True)
    else:
        _client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    _client.ping()
    return _client
