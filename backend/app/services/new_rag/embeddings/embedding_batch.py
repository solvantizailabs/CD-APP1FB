"""
Batching helper for embedding requests. Chunks are embedded in fixed-size
batches rather than one request for an entire chapter's chunk list - a
chapter's full chunk list previously went out as a single embeddings.create
call, which was the request most likely to trip the shared account-wide TPM
limit (see rate_governor.py), and unlike the other LLM call sites it had no
retry/backoff at all before this was fixed.
"""
from typing import Iterator, List, TypeVar

T = TypeVar("T")

EMBED_BATCH_SIZE = 100


def batches(items: List[T], batch_size: int = EMBED_BATCH_SIZE) -> Iterator[List[T]]:
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]
