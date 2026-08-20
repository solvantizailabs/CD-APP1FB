"""
Hybrid retrieval orchestration for the new RAG pipeline. See
docs/RAG_REDESIGN_PLAN.md, section 8, and docs/RAG_SPEC_ALIGNMENT_PLAN.md,
Phase 5.

Locked design implemented here:
- Widen initial candidates to top-20 (dense + sparse) before fusion.
- RRF fusion (kept - proven fix for the old min-max normalization bug).
- Dedup near-identical candidates before spending rerank time on them
  (context/deduplicator.py).
- Cross-encoder rerank over the deduped candidates (local model, no added
  LLM token cost).
- Dynamic Top-K by a local question-complexity heuristic (CTO spec section
  16) - a real intent-classification signal belongs upstream per section
  1.2's deferred decision; this is a cheap local stand-in, not a claim of
  real NLU.
- 4-tier confidence (HIGH/MEDIUM/LOW/INSUFFICIENT, CTO spec section 21)
  from rerank score plus fusion/rerank agreement, not one raw threshold.
- Adaptive retrieval depth: escalate to the full parent topic when a clear
  majority of the top results share one parent_chunk_id.
- Bounded single retry on low confidence (never more than two searches).
- Explicit confidence-status contract: RAG returns a status, it does not
  decide what the student sees - that's an orchestrator decision, deferred
  (see docs/RAG_SPEC_ALIGNMENT_PLAN.md, section 1.2).
"""
import logging
import os
from collections import Counter
from typing import Dict, List, Optional

from qdrant_client import models

from backend.app.services.new_rag.embeddings.embedding_service import get_openai_client
from backend.app.services.new_rag.indexing.qdrant_indexer import COLLECTION_NAME, get_qdrant_client
from backend.app.services.new_rag.retrieval import semantic_retriever, keyword_retriever
from backend.app.services.new_rag.retrieval.metadata_filter import build_filter
from backend.app.services.new_rag.reranking.reranker import rerank
from backend.app.services.new_rag.context.deduplicator import deduplicate
from backend.app.services.new_rag.context.compressor import compress
from backend.app.services.new_rag.context.context_builder import build_context_package
from backend.app.services.new_rag import local_artifacts, supabase_artifacts

logger = logging.getLogger(__name__)

INITIAL_TOP_K = 20
PARENT_ESCALATION_MIN_SHARE_RATIO = 0.6  # fraction of the dynamic top-K sharing one parent triggers escalation

# --- Confidence tiers (CTO spec section 21) ---
# All three cutoffs are placeholders pending empirical calibration once live
# query data exists, per plan doc section 8 - same methodology as the
# existing calibrated STUDENT_HISTORY_MIN_SCORE / GLOBAL_CACHE_MIN_SCORE
# thresholds elsewhere in this codebase. MEDIUM_THRESHOLD is the same value
# the old single-tier CONFIDENCE_THRESHOLD used, kept as the
# confident/insufficient boundary for backward continuity; HIGH and LOW are
# new tiers layered around it, not a re-guess of the whole scale.
HIGH_THRESHOLD = 0.0
MEDIUM_THRESHOLD = -5.0
LOW_THRESHOLD = -8.0
CONFIDENCE_THRESHOLD = MEDIUM_THRESHOLD  # kept as the retry/insufficient gate, same as before

# --- Dynamic Top-K (CTO spec section 16) ---
# Ranges taken directly from the spec ("simple factual: top 2-4, conceptual:
# top 4-6, complex: top 6-10, multi-concept: merge multiple groups") - the
# upper bound of each range is used as this pipeline's dynamic K.
TOP_K_BY_COMPLEXITY = {
    "simple": 4,
    "conceptual": 6,
    "complex": 10,
    "multi_concept": 10,
}
_MULTI_CONCEPT_MARKERS = (" and ", " both ", " compare ", " versus ", " vs ", " relationship between ")
_CONCEPTUAL_MARKERS = ("explain", "why", "how does", "how do", "describe", "what happens")
_SIMPLE_MARKERS = ("what is", "define", "who is", "when did", "which")


def classify_query_complexity(query: str) -> str:
    """
    Cheap local heuristic (word count + question-form markers), not real
    intent classification - a stand-in until the orchestrator's Stage 1 (or
    a future query/intent_classifier.py, per section 1.2's deferred
    decision) provides a real signal. Errs toward "conceptual" as the
    middle-ground default when nothing matches, rather than the smallest or
    largest K, since guessing too small silently starves a real answer and
    guessing too large just costs a few extra tokens rather than breaking
    anything.
    """
    q = (query or "").strip().lower()
    word_count = len(q.split())

    if any(marker in q for marker in _MULTI_CONCEPT_MARKERS) and word_count > 6:
        return "multi_concept"
    if word_count > 20:
        return "complex"
    if any(q.startswith(marker) or f" {marker}" in q for marker in _SIMPLE_MARKERS) and word_count <= 8:
        return "simple"
    if any(marker in q for marker in _CONCEPTUAL_MARKERS):
        return "conceptual"
    if word_count <= 6:
        return "simple"
    return "conceptual"


def _dynamic_top_k(query: str) -> int:
    complexity = classify_query_complexity(query)
    return TOP_K_BY_COMPLEXITY[complexity]


def compute_confidence_tier(candidates: List[Dict], top_score: Optional[float]) -> str:
    """
    HIGH requires both a strong rerank score AND agreement between the
    reranker's top pick and the fused (semantic+keyword) top-3 - a single
    strong score alone isn't enough evidence that multiple signals actually
    agree, which is the spec's stated definition of HIGH confidence.
    """
    if not candidates or top_score is None:
        return "INSUFFICIENT"

    top_candidate_id = candidates[0].get("payload", {}).get("chunk_id")
    fusion_sorted = sorted(candidates, key=lambda c: c.get("fusion_score", 0), reverse=True)
    fusion_top3_ids = {c.get("payload", {}).get("chunk_id") for c in fusion_sorted[:3]}
    agrees = top_candidate_id in fusion_top3_ids

    if top_score >= HIGH_THRESHOLD and agrees:
        return "HIGH"
    if top_score >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    if top_score >= LOW_THRESHOLD:
        return "LOW"
    return "INSUFFICIENT"


def _hybrid_search_once(client, openai_client, query: str, book_uuid: str,
                         chapter_id: Optional[str] = None, topic_id: Optional[str] = None) -> List[Dict]:
    query_filter = build_filter(book_uuid, chapter_id=chapter_id, topic_id=topic_id)

    result = client.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            semantic_retriever.build_prefetch(openai_client, query, query_filter),
            keyword_retriever.build_prefetch(query, query_filter),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=INITIAL_TOP_K,
        with_payload=True,
    )
    return [{"payload": p.payload, "fusion_score": p.score} for p in result.points]


def _load_parents_lookup(class_name: str, subject: str) -> Dict:
    """
    Supabase Storage is the primary source (production-durable, per
    docs/RAG_REDESIGN_PLAN.md section 7 - local disk alone does not survive
    a Render redeploy, confirmed by this project's own history). Local disk
    is the fallback, not the primary - kept so a purely local dev/test
    setup without Supabase credentials configured still works, but real
    deployments should be reading the Supabase copy every time.
    """
    book_dir = local_artifacts.book_dir(class_name, subject)
    book_key = book_dir.split(os.sep)[-1] if os.sep in book_dir else book_dir.rsplit("/", 1)[-1]
    remote = supabase_artifacts.download_json(f"{book_key}/parents_lookup.json")
    if remote is not None:
        return remote
    logger.info("[NEW_RAG][Retrieval] Supabase parents_lookup unavailable, falling back to local disk.")
    return local_artifacts.load_json(book_dir, "parents_lookup.json") or {}


def _maybe_escalate_to_parent(top_children: List[Dict], class_name: str, subject: str, top_k: int) -> Dict:
    """
    If a clear share of the top results concentrate on one or more parent
    topics, return those parents' full text instead of the scattered
    individual children - we already have a coherent, complete unit, no
    need to reconstruct it from fragments.

    Escalates to EVERY parent that independently meets the share threshold,
    not just the single most common one - confirmed live that a genuine
    even split (e.g. 2-and-2 across two clearly relevant topics for a real
    multi-concept question) previously collapsed to just one parent via
    Counter.most_common(1), silently discarding the other equally-relevant
    topic. A question that's genuinely about two topics should be able to
    return two parents.

    Reads parent text via _load_parents_lookup (Supabase primary, local
    disk fallback). Parents are never embedded, so this is a lookup, not a
    vector search.
    """
    top_n = top_children[:top_k]
    # child_candidates is returned regardless of whether escalation triggers,
    # so a caller (the CLI, a debugging report) always has visibility into
    # the actual child-level matches that were considered - previously this
    # was discarded the moment escalation fired, which meant "why did it
    # escalate" and "what would the un-escalated answer have looked like"
    # were both unanswerable after the fact.
    parent_counts = Counter(c["payload"]["parent_chunk_id"] for c in top_n)
    threshold = max(1, round(top_k * PARENT_ESCALATION_MIN_SHARE_RATIO))
    qualifying_parent_ids = [pid for pid, count in parent_counts.most_common() if count >= threshold]

    if not qualifying_parent_ids:
        return {"escalated": False, "chunks": top_n, "child_candidates": top_n}

    parents_lookup = _load_parents_lookup(class_name, subject)
    escalated_chunks = []
    for pid in qualifying_parent_ids:
        parent = parents_lookup.get(pid)
        if not parent:
            logger.warning(f"[NEW_RAG][Retrieval] Escalation triggered but parent {pid} not found in lookup.")
            continue
        best_child_score = max(c["rerank_score"] for c in top_n if c["payload"]["parent_chunk_id"] == pid)
        escalated_chunks.append({
            "payload": parent,
            "rerank_score": best_child_score,
            "note": f"escalated to full parent topic ({parent_counts[pid]}/{len(top_n)} of top results)",
        })

    if not escalated_chunks:
        return {"escalated": False, "chunks": top_n, "child_candidates": top_n}

    escalated_chunks.sort(key=lambda c: c["rerank_score"], reverse=True)
    return {
        "escalated": True,
        "dominant_parent_share": f"{parent_counts[qualifying_parent_ids[0]]}/{len(top_n)}",
        "escalated_parent_count": len(escalated_chunks),
        "chunks": escalated_chunks,
        "child_candidates": top_n,
    }


def retrieve(query: str, book_uuid: str, class_name: str = "", subject: str = "",
             chapter_id: Optional[str] = None, topic_id: Optional[str] = None) -> Dict:
    """
    `chapter_id`/`topic_id` are optional narrowing filters, per CTO spec
    section 9 ("use metadata filtering aggressively... before performing
    broad retrieval whenever the information is known with sufficient
    confidence"). Currently only book_uuid is resolved upstream by the CLI's
    test harness - a real caller that has already resolved the chapter (the
    orchestrator's Stage 1 already does this in the live app, see
    docs/RAG_SPEC_ALIGNMENT_PLAN.md section 1.2) can pass it here to narrow
    the search before it ever runs, rather than filtering after the fact.
    """
    top_k = _dynamic_top_k(query)
    logger.info(f"[NEW_RAG][Retrieval] query complexity={classify_query_complexity(query)!r} -> top_k={top_k}")

    client = get_qdrant_client()
    openai_client = get_openai_client()

    candidates = _hybrid_search_once(client, openai_client, query, book_uuid, chapter_id, topic_id)
    candidates = deduplicate(candidates)
    candidates = rerank(query, candidates)
    top_score = candidates[0]["rerank_score"] if candidates else None
    confidence_tier = compute_confidence_tier(candidates, top_score)
    retried = False

    if confidence_tier in ("LOW", "INSUFFICIENT"):
        retried = True
        logger.info(f"[NEW_RAG][Retrieval] Low confidence (tier={confidence_tier}, top_score={top_score}); bounded single retry.")
        candidates = _hybrid_search_once(client, openai_client, query, book_uuid, chapter_id, topic_id)
        candidates = deduplicate(candidates)
        candidates = rerank(query, candidates)
        top_score = candidates[0]["rerank_score"] if candidates else None
        confidence_tier = compute_confidence_tier(candidates, top_score)

    # full_candidate_pool is the entire deduped+reranked pool (up to
    # INITIAL_TOP_K=20, before the top_k cut used for the actual answer) -
    # kept on every return path so a debugging report can see not just what
    # won, but everything that competed, not only the final top-k.
    if not candidates:
        return {
            "status": "insufficient_context",
            "confidence_tier": "INSUFFICIENT",
            "best_attempt_chunks": [],
            "full_candidate_pool": [],
            "top_score": None,
            "retried": retried,
            "top_k": top_k,
        }

    if confidence_tier == "INSUFFICIENT":
        return {
            "status": "insufficient_context",
            "confidence_tier": confidence_tier,
            "best_attempt_chunks": candidates[:top_k],
            "full_candidate_pool": candidates,
            "top_score": top_score,
            "retried": retried,
            "top_k": top_k,
        }

    escalation = _maybe_escalate_to_parent(candidates, class_name, subject, top_k)

    return {
        "status": "confident",
        "confidence_tier": confidence_tier,
        "chunks": escalation["chunks"],
        "escalated_to_parent": escalation["escalated"],
        "escalated_parent_count": escalation.get("escalated_parent_count", 0),
        # The child-level candidates that were actually searched/reranked,
        # preserved even when escalation swaps the final answer to a parent -
        # without this, "what were the underlying children, and why did this
        # escalate" was unanswerable after the fact.
        "child_candidates": escalation["child_candidates"],
        "full_candidate_pool": candidates,
        "top_score": top_score,
        "retried": retried,
        "top_k": top_k,
    }


def retrieve_as_package(query: str, book_uuid: str, class_name: str = "", subject: str = "",
                         chapter_id: Optional[str] = None, topic_id: Optional[str] = None,
                         resolved_query: Optional[str] = None, intent: Optional[str] = None,
                         chapter: Optional[str] = None, topic: Optional[str] = None,
                         concept: Optional[str] = None) -> Dict:
    """
    The CTO spec's exact RAG context package (section 19) - calls retrieve()
    (unchanged, still used directly by the CLI's own simpler contract), then
    compresses the result and assembles it into the package shape. Kept as
    a separate function rather than changing retrieve()'s own return shape,
    so existing callers (cli.py) don't break - this is the new, additional
    entrypoint a real caller wanting the spec's exact contract should use.
    """
    result = retrieve(query, book_uuid, class_name, subject, chapter_id, topic_id)
    chunks = result.get("chunks", result.get("best_attempt_chunks", []))
    compressed_chunks, total_tokens = compress(query, chunks)
    logger.info(f"[NEW_RAG][Context] Compressed {len(compressed_chunks)} chunk(s) to ~{total_tokens} tokens.")

    if "chunks" in result:
        result["chunks"] = compressed_chunks
    else:
        result["best_attempt_chunks"] = compressed_chunks

    return build_context_package(query, result, class_name, subject, resolved_query, intent, chapter, topic, concept)
