"""
Qdrant storage for Stage 2's image-vector collection (see
docs/IMAGE_PIPELINE_PLAN.md section 3). Deliberately a SEPARATE collection
from textbooks_v3, not a blended one - CLIP's text-image similarity scores
are not on the same scale as textbooks_v3's dense/sparse text scores
(confirmed via research: even with CLIP unifying text and images into one
space, text-text and text-image comparisons have incompatible score
ranges), so merging the two happens at retrieval time via rank-based
fusion (RRF over each collection's own ranked list), never by comparing
raw scores across collections directly.

Each point's id is the SAME chunk_id as its caption's point in
textbooks_v3 - a deliberate join key, not incidental, so "does this
diagram also have a strong image-similarity match" is a trivial ID lookup
against textbooks_v3's payload rather than a separate mapping table.
"""
import os
import logging
from typing import Dict, List, Optional

from qdrant_client import QdrantClient, models

from backend.app.services.new_rag.embeddings.image_embedding_service import CLIP_DIM
from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client  # noqa: F401 - re-exported for callers

logger = logging.getLogger(__name__)

IMAGE_COLLECTION_NAME = os.environ.get("NEW_RAG_IMAGE_COLLECTION_NAME", "textbook_diagrams_v1")


def ensure_image_collection(client: QdrantClient):
    if client.collection_exists(collection_name=IMAGE_COLLECTION_NAME):
        return
    client.create_collection(
        collection_name=IMAGE_COLLECTION_NAME,
        vectors_config=models.VectorParams(size=CLIP_DIM, distance=models.Distance.COSINE),
    )
    for field in ["book_uuid", "chapter_id", "topic_id", "parent_chunk_id"]:
        try:
            client.create_payload_index(
                collection_name=IMAGE_COLLECTION_NAME, field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass
    logger.info(f"[NEW_RAG][ImageIndex] Created collection '{IMAGE_COLLECTION_NAME}' (dim={CLIP_DIM}).")


def upsert_diagram_images(client: QdrantClient, diagram_chunks: List[Dict], image_vectors: List[List[float]],
                           book_uuid: str, class_name: str, subject: str) -> int:
    """
    diagram_chunks: the SAME chunk dicts already built for textbooks_v3
    (chunk_id, chapter_id, topic_id, parent_chunk_id, chapter_name,
    topic_name, structured_content, text=caption, start_page) - reused
    as-is rather than re-deriving a parallel schema, so the two
    collections' payloads stay trivially joinable by chunk_id.
    book_uuid/class_name/subject are passed explicitly since (unlike the
    textbooks_v3 path) diagram_chunks don't carry these themselves - they're
    stamped onto every point by the caller in rag_pipeline.py the same way
    qdrant_indexer.upsert_chunks() does for the text collection.
    """
    ensure_image_collection(client)
    if not diagram_chunks:
        return 0
    points = []
    for chunk, vector in zip(diagram_chunks, image_vectors):
        payload = {
            "chunk_id": chunk["chunk_id"],
            "book_uuid": book_uuid,
            "class_name": class_name,
            "subject": subject,
            "chapter_id": chunk.get("chapter_id"),
            "chapter_name": chunk.get("chapter_name"),
            "topic_id": chunk.get("topic_id"),
            "topic_name": chunk.get("topic_name"),
            "parent_chunk_id": chunk.get("parent_chunk_id"),
            "page_number": chunk.get("start_page"),
            "image_url": chunk.get("structured_content"),
            "caption": chunk.get("text"),
        }
        points.append(models.PointStruct(id=chunk["chunk_id"], vector=vector, payload=payload))
    client.upsert(collection_name=IMAGE_COLLECTION_NAME, points=points)
    logger.info(f"[NEW_RAG][ImageIndex] Upserted {len(points)} image vectors into '{IMAGE_COLLECTION_NAME}'.")
    return len(points)


def search_images(client: QdrantClient, query_vector: List[float], book_uuid: str,
                   chapter_id: Optional[str] = None, limit: int = 5) -> List[Dict]:
    """
    Cross-modal search: query_vector is a CLIP TEXT embedding
    (image_embedding_service.embed_text_query), compared against CLIP IMAGE
    embeddings stored here. Returns [] (not an error) if the collection
    doesn't exist yet - a book ingested before Stage 2 was built simply has
    no image-vector results, same "nothing to find" contract as an empty
    collection.
    """
    if not client.collection_exists(collection_name=IMAGE_COLLECTION_NAME):
        return []
    must = [models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
    if chapter_id:
        must.append(models.FieldCondition(key="chapter_id", match=models.MatchValue(value=chapter_id)))
    results = client.query_points(
        collection_name=IMAGE_COLLECTION_NAME,
        query=query_vector,
        query_filter=models.Filter(must=must),
        limit=limit,
        with_payload=True,
    )
    return [{"payload": p.payload, "score": p.score} for p in results.points]
