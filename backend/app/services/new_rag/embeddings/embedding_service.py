"""
Dense embedding generation for the new RAG pipeline. See
docs/RAG_REDESIGN_PLAN.md, section 7.

Locked decision: OpenAI text-embedding-3-small for dense vectors (kept as
the single production embedder - no change, since a full re-embed was
already mandatory for the new chunking pipeline regardless).

Sparse/keyword embedding lives in indexing/keyword_indexer.py, not here -
dense (semantic) and sparse (keyword) are separate concerns even though
both ultimately get stored on the same Qdrant point.
"""
import os
import logging
from typing import Dict, List, Optional

from openai import OpenAI

from backend.app.services.new_rag.retry import call_with_retry
from backend.app.services.new_rag import rate_governor
from backend.app.services.new_rag.embeddings.embedding_batch import batches

logger = logging.getLogger(__name__)

DENSE_MODEL = "text-embedding-3-small"
DENSE_DIM = 1536


def get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def format_for_embedding(chunk: Dict, class_name: str, subject: str) -> str:
    """
    Prefixes educational context onto a chunk's raw text before it's
    embedded (CTO spec, section 8: "preferable to embedding raw textbook
    text alone because the embedding carries educational context"). Only
    the embedding INPUT is enriched this way - the chunk's own stored
    `text`/`content` payload stays the clean, unprefixed original, since
    that's what a student or developer actually reads back.

    Missing fields (e.g. topic/concept not yet populated - see Phase 1's
    honest None for subtopic/concept) are simply omitted from the prefix
    rather than printed as "None", so the embedding input never contains
    literal placeholder noise.
    """
    lines = [f"Subject: {subject}", f"Class: {class_name}"]
    if chunk.get("chapter_name"):
        lines.append(f"Chapter: {chunk['chapter_name']}")
    if chunk.get("topic") or chunk.get("topic_name"):
        lines.append(f"Topic: {chunk.get('topic') or chunk.get('topic_name')}")
    if chunk.get("concept"):
        lines.append(f"Concept: {chunk['concept']}")
    if chunk.get("chunk_type"):
        lines.append(f"Type: {chunk['chunk_type']}")
    lines.append("")
    lines.append(chunk.get("text", ""))
    return "\n".join(lines)


def embed_dense(openai_client: OpenAI, texts: List[str]) -> List[List[float]]:
    all_embeddings: List[List[float]] = []
    for batch in batches(texts):
        rate_governor.reserve(rate_governor.estimate_text_tokens(sum(len(t) for t in batch)))
        response = call_with_retry(
            lambda b=batch: openai_client.embeddings.create(input=b, model=DENSE_MODEL)
        )
        all_embeddings.extend(item.embedding for item in response.data)
    return all_embeddings
