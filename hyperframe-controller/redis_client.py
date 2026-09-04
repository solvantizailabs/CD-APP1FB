import os
import redis


def get_redis_client():
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        client = redis.from_url(redis_url, decode_responses=True)
    else:
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    client.ping()
    return client


def queue_depth(r) -> int:
    """Waiting + in-flight - same definition as backend/redis/queue.py's
    queue_depth(), duplicated here since the Controller is a standalone
    deployable and shouldn't import backend.*."""
    return r.llen("hyperframe:video_jobs:queue") + r.scard("hyperframe:video_jobs:inflight")
