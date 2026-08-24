"""
Adapter bridging new_rag's retrieve() (dict-based, confidence-tiered,
cross-encoder-reranked) into the shape backend/app/orchestrator_test/
test_runner.py already expects from the old qdrant_service.hybrid_search()
(a list of (score, payload) tuples), per docs/RAG_INTEGRATION_PLAN.md §4.2.

Deliberately a separate, additive module rather than a change inside
qdrant_service.py itself - qdrant_service.py (and the textbooks_v2
collection it serves) is left untouched, same additive-only convention
new_rag itself was built under. This is the ONLY module that imports both
new_rag's retrieval internals and gets called from the live orchestrator
path - i.e. the actual seam where the "process swap" happens for retrieval.
"""
import logging
from typing import Dict, List, Optional, Tuple

from qdrant_client import models

from backend.app.services.new_rag.retrieval.hybrid_retriever import retrieve
from backend.app.services.new_rag.indexing.qdrant_indexer import COLLECTION_NAME, get_qdrant_client

logger = logging.getLogger(__name__)


def book_has_content(book_uuid: str) -> bool:
    """
    textbooks_v3-aware equivalent of qdrant_service.book_has_content() (which
    only ever checked the old textbooks_v2 collection). Used by
    resolve_book_uuid_for_subject() so a Firestore chapter-summary doc with
    metadata but zero actual textbooks_v3 vectors still correctly downgrades
    to GENERAL_KNOWLEDGE instead of resolving to an empty book.
    """
    try:
        client = get_qdrant_client()
        if not client.collection_exists(collection_name=COLLECTION_NAME):
            return False
        count = client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
            ),
            exact=False,
        ).count
        return count > 0
    except Exception as e:
        logger.warning(f"[NEW_RAG][Adapter] book_has_content check failed for {book_uuid}: {e}")
        return False


def hybrid_search_v2(query: str, book_uuid: str, class_name: str = "", subject: str = "",
                      chapter_id: Optional[str] = None) -> Dict:
    """
    Runs new_rag's full retrieve() (hybrid fusion -> dedup -> rerank ->
    confidence tier -> bounded retry -> parent escalation) and returns both:
    - "score_payload_pairs": a List[Tuple[score, payload_dict]], the same
      shape test_runner.py already unpacks from the old hybrid_search()'s
      first return value, so the call site only needs the tuple's SOURCE
      swapped, not its consumption logic.
    - the full confidence/status contract (status, confidence_tier,
      top_score, retried, escalated_to_parent) - new information the old
      hybrid_search() never provided, used to replace the two hardcoded
      raw-score thresholds test_runner.py used to rely on (0.55 chapter-
      retry gate, 0.05 grounding-quality gate - see docs/RAG_INTEGRATION_PLAN.md
      §4.2, both tuned for the old 0-1 RRF scale and meaningless against
      new_rag's cross-encoder logit scale).

    chapter_id narrows the search server-side (via new_rag's own metadata
    filter) instead of the old approach of searching the whole book and
    filtering by chapter_name after the fact.
    """
    result = retrieve(query, book_uuid, class_name=class_name, subject=subject, chapter_id=chapter_id)

    raw_candidates = result.get("chunks") or result.get("best_attempt_chunks") or []
    score_payload_pairs: List[Tuple[float, Dict]] = [
        (c.get("rerank_score", 0.0), c.get("payload", {})) for c in raw_candidates
    ]

    return {
        "score_payload_pairs": score_payload_pairs,
        "status": result.get("status"),
        "confidence_tier": result.get("confidence_tier"),
        "top_score": result.get("top_score"),
        "retried": result.get("retried", False),
        "escalated_to_parent": result.get("escalated_to_parent", False),
        "raw_result": result,  # full new_rag result, for the per-query debug record (§9)
    }
