"""
Qdrant storage for the new RAG pipeline. See docs/RAG_REDESIGN_PLAN.md,
section 7.

Locked decision: a brand new collection (textbooks_v3), never touching the
existing textbooks_v2 collection the live app still serves from.
"""
import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from openai import OpenAI
from qdrant_client import QdrantClient, models

from backend.app.services.new_rag.embeddings.embedding_service import embed_dense, format_for_embedding
from backend.app.services.new_rag.indexing.keyword_indexer import embed_sparse

logger = logging.getLogger(__name__)

COLLECTION_NAME = os.environ.get("NEW_RAG_COLLECTION_NAME", "textbooks_v3")


def get_qdrant_client() -> QdrantClient:
    url = os.getenv("QDRANT_URL")
    api_key = os.getenv("QDRANT_API_KEY")
    if not url:
        raise RuntimeError("QDRANT_URL not set")
    kwargs = {"timeout": 60}
    if api_key:
        kwargs["api_key"] = api_key
    if "qdrant.io" in url or "cloud" in url:
        url = url.replace(":6333", "").replace(":6334", "")
        kwargs["port"] = 443
        kwargs["prefer_grpc"] = False
    return QdrantClient(url=url, **kwargs)


def ensure_collection(client: QdrantClient):
    if client.collection_exists(collection_name=COLLECTION_NAME):
        return
    from backend.app.services.new_rag.embeddings.embedding_service import DENSE_DIM
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={"dense": models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    for field in ["book_uuid", "chapter_id", "topic_id", "chunk_type", "parent_chunk_id"]:
        try:
            client.create_payload_index(
                collection_name=COLLECTION_NAME, field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass
    logger.info(f"[NEW_RAG] Created collection '{COLLECTION_NAME}' with dense+sparse vectors.")


def upsert_chunks(client: QdrantClient, openai_client: OpenAI, chunks: List[Dict],
                   book_uuid: str, class_name: str, subject: str,
                   document_name: Optional[str] = None) -> int:
    """Embeds and upserts child chunks only - parents are not embedded (see plan doc)."""
    ensure_collection(client)
    if not chunks:
        return 0
    raw_texts = [c["text"] for c in chunks]
    # Dense (semantic) embedding input is context-enriched per CTO spec section 8;
    # sparse (keyword/BM25) embedding deliberately stays on raw text - injecting
    # the same "Subject: Science\nClass: 10..." prefix into every chunk would add
    # recurring generic terms to BM25's term-frequency statistics, diluting exact
    # keyword relevance rather than helping it. Enrichment only helps the
    # semantic side, so only the semantic side gets it.
    enriched_texts = [format_for_embedding(c, class_name, subject) for c in chunks]
    dense_vecs = embed_dense(openai_client, enriched_texts)
    sparse_vecs = embed_sparse(raw_texts)

    # Book/chapter-level metadata (docs/RAG_SPEC_ALIGNMENT_PLAN.md, section 3)
    # that's the same for every chunk in this upsert call, computed once
    # rather than per-chunk. `curriculum` is hard-coded "NCERT" since that's
    # the only curriculum this pipeline currently ingests (see spec section
    # 3's own scope) - a real field, not a guess, given the current scope.
    now_iso = datetime.now(timezone.utc).isoformat()
    book_level_fields = {
        "book": f"Class {class_name} {subject}",
        "part": None,
        "curriculum": "NCERT",
        "class": class_name,
        "source_version": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    points = []
    for chunk, dense_vec, sparse_vec in zip(chunks, dense_vecs, sparse_vecs):
        payload = dict(chunk)
        payload["book_uuid"] = book_uuid
        payload["class_name"] = class_name
        payload["subject"] = subject
        payload["document_id"] = chunk.get("chapter_id")
        payload["document_name"] = document_name
        payload["content"] = chunk.get("text")
        payload.update(book_level_fields)
        points.append(models.PointStruct(
            id=chunk["chunk_id"],
            vector={
                "dense": dense_vec,
                "sparse": models.SparseVector(
                    indices=sparse_vec.indices.tolist(),
                    values=sparse_vec.values.tolist(),
                ),
            },
            payload=payload,
        ))
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info(f"[NEW_RAG] Upserted {len(points)} chunks for book_uuid={book_uuid} into '{COLLECTION_NAME}'.")
    return len(points)
