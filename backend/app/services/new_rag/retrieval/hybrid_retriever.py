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


def _image_augmented_chunk_ids(query: str, book_uuid: str, chapter_id: Optional[str], limit: int = 5) -> List[str]:
    """
    Cross-modal widening (docs/IMAGE_PIPELINE_PLAN.md section 3.4/9.1): finds
    diagrams whose IMAGE is visually relevant to the query even when their
    caption's wording doesn't semantically match it - the dense+sparse
    search in _hybrid_search_once only ever finds a diagram via its caption
    text; this is a second, independent discovery signal against the same
    diagram's actual pixels (textbook_diagrams_v1, CLIP embeddings).

    Returns chunk_ids only, not full candidates - the caller fetches each
    one's real payload from textbooks_v3 by ID, so every candidate that
    reaches dedup/rerank has the identical payload shape (chunk_type, text,
    structured_content, ...) regardless of which search found it, rather
    than mixing in image_indexer's narrower collection schema.

    Fails open (returns []) rather than raising - image-vector widening is
    an enhancement on top of retrieval that already worked before Stage 2
    existed; a missing/broken image collection (e.g. a book ingested before
    Stage 2 was built) must never block or degrade the core text/caption
    search above.
    """
    try:
        from backend.app.services.new_rag.embeddings.image_embedding_service import embed_text_query
        from backend.app.services.new_rag.indexing import image_indexer

        client = image_indexer.get_qdrant_client()
        query_vector = embed_text_query(query)
        hits = image_indexer.search_images(client, query_vector, book_uuid, chapter_id=chapter_id, limit=limit)
        return [h["payload"]["chunk_id"] for h in hits if h["payload"].get("chunk_id")]
    except Exception as e:
        logger.warning(f"[NEW_RAG][Retrieval] Image-vector search skipped (non-fatal): {e}")
        return []


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
    candidates = [{"payload": p.payload, "fusion_score": p.score} for p in result.points]

    # Stage 2 image-vector widening - only ADDS diagrams missing from the
    # pool above, never re-scores or reorders what fusion already found.
    # Final order is still decided entirely by the cross-encoder reranker in
    # retrieve() just below, which scores every candidate's actual text
    # uniformly regardless of which search found it - so this never needs
    # to compare CLIP's image-similarity score against textbooks_v3's
    # fusion score directly (those scales are not comparable, see
    # docs/IMAGE_PIPELINE_PLAN.md section 3.1).
    existing_ids = {c["payload"].get("chunk_id") for c in candidates}
    image_chunk_ids = [cid for cid in _image_augmented_chunk_ids(query, book_uuid, chapter_id)
                        if cid not in existing_ids]
    if image_chunk_ids:
        fetched = client.retrieve(collection_name=COLLECTION_NAME, ids=image_chunk_ids, with_payload=True)
        # No real fusion score exists for a candidate fusion never found -
        # deliberately set to the pool's own minimum rather than 0 or a
        # guess, so it never LOOKS like it competed well in the fusion
        # stage (compute_confidence_tier's HIGH-tier agreement check
        # correctly never counts an image-only find), while still being
        # fully eligible for reranking just below.
        min_fusion_score = min((c["fusion_score"] for c in candidates), default=0.0)
        for point in fetched:
            candidates.append({"payload": point.payload, "fusion_score": min_fusion_score, "found_via": "image_vector"})
        logger.info(f"[NEW_RAG][Retrieval] Image-vector search added {len(fetched)} diagram(s) "
                    f"not found by text/caption search.")

    return candidates


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

    # Full per-parent vote breakdown (2026-08-25, added for debugging
    # visibility per user request): every distinct parent topic among top_n,
    # its vote count, and whether it cleared the threshold - not just the
    # winner(s). Confirmed live this matters: a real split like 6/3/1 across
    # three parent topics only lets the 6-group escalate; the 3-group and
    # the 1-group are otherwise invisible in the output even though they're
    # a meaningful chunk of the evidence the reranker considered. Returned
    # on every path (escalated or not) so a caller can always see the full
    # picture, not just the outcome.
    topic_names_by_pid = {c["payload"]["parent_chunk_id"]: c["payload"].get("topic_name")
                           for c in top_n}
    parent_vote_breakdown = [
        {
            "parent_chunk_id": pid,
            "topic_name": topic_names_by_pid.get(pid),
            "vote_count": count,
            "share": f"{count}/{len(top_n)}",
            "qualifies_for_escalation": count >= threshold,
        }
        for pid, count in parent_counts.most_common()
    ]

    if not qualifying_parent_ids:
        for c in top_n:
            c["level"] = "child"
        return {
            "escalated": False, "chunks": top_n, "child_candidates": top_n,
            "parent_vote_breakdown": parent_vote_breakdown, "escalation_threshold_count": threshold,
        }

    parents_lookup = _load_parents_lookup(class_name, subject)
    escalated_chunks = []
    covered_parent_ids = set()
    for pid in qualifying_parent_ids:
        parent = parents_lookup.get(pid)
        if not parent:
            logger.warning(f"[NEW_RAG][Retrieval] Escalation triggered but parent {pid} not found in lookup.")
            continue
        best_child_score = max(c["rerank_score"] for c in top_n if c["payload"]["parent_chunk_id"] == pid)
        escalated_chunks.append({
            "payload": parent,
            "rerank_score": best_child_score,
            "level": "parent",
            "note": f"escalated to full parent topic ({parent_counts[pid]}/{len(top_n)} of top results)",
        })
        covered_parent_ids.add(pid)

        # Bug fix (2026-08-25, found via live testing - docs/IMAGE_PIPELINE_PLAN.md):
        # escalation used to swap EVERY child under this parent for the parent's
        # plain text, silently discarding any diagram chunk that was part of the
        # escalated group - including cases where the diagram was the single
        # highest-scoring candidate of the whole search. The parent's own text
        # never contains the image itself (diagrams are separate chunk_type
        # entries, not inlined into parent text), so escalating to parent text
        # is not a superset of a diagram child - it's a genuine loss. Re-attach
        # any diagram chunk(s) that were part of THIS parent's escalated group,
        # alongside the escalated parent text, so ground_text_narration() still
        # has a real image to attach at generation time. Deduped by chunk_id in
        # case the same diagram appears more than once in top_n.
        #
        # Bug fix (2026-08-25, found via live testing against a real question -
        # jess101.pdf, "What does the diagram that classifies resources into
        # renewable and non-renewable types show?"): re-attachment used to pull
        # in EVERY diagram under the escalated parent regardless of that
        # diagram's own score - confirmed live this flooded results with junk
        # (a QR code, a decorative title banner, both scoring -8 to -11) purely
        # because they shared a parent topic with the winning group, not
        # because any of them were individually relevant to the question.
        # Checked docs/IMAGE_PIPELINE_PLAN.md and friends first (per user
        # request) - no relevance floor for this was ever documented, so this
        # isn't a fix to a documented gap, it's new ground, designed to match
        # the existing documented philosophy that image-vector search only
        # ever WIDENS the candidate pool and never gets treated as
        # automatically authoritative (IMAGE_PIPELINE_PLAN.md section 3.4).
        # Reuses LOW_THRESHOLD (already calibrated for confidence tiering
        # elsewhere in this file) rather than inventing a new constant - a
        # diagram must clear at least LOW confidence on its own merits, not
        # just share a parent with chunks that do.
        seen_diagram_ids = set()
        for c in top_n:
            p = c["payload"]
            if p.get("parent_chunk_id") != pid or p.get("chunk_type") != "diagram":
                continue
            if c["rerank_score"] < LOW_THRESHOLD:
                continue
            cid = p.get("chunk_id")
            if cid in seen_diagram_ids:
                continue
            seen_diagram_ids.add(cid)
            c["level"] = "child"
            escalated_chunks.append(c)

    # Bug fix (2026-08-25, found via live testing against a real question -
    # jess101.pdf, "What is sustainable development, and what was agreed at
    # the 1992 Rio de Janeiro Earth Summit?"): the single highest-scoring
    # candidate in the ENTIRE pool (rerank_score +5.03, genuinely the right
    # answer) was silently discarded because it was the only one of its
    # parent topic in top_n - a different, mostly-irrelevant topic
    # ("Introduction", best score -9.7, padded out by low-value diagram
    # chunks like a QR code and a decorative title banner) won the
    # majority-vote-by-COUNT threshold purely by outnumbering it 6-to-1, with
    # no check on whether that winning group's own scores were any good.
    # The escalated result ended up entirely composed of chunks scoring
    # -9.7 to -11, while the one candidate that actually answered the
    # question vanished - and the top-level `top_score` returned to the
    # caller (computed from the pre-escalation pool) kept reporting +5.03
    # regardless, making the mismatch invisible until the actual chunks were
    # inspected. A majority vote by raw count, with no score-quality floor
    # and no protection for the single best match, is not a safe way to
    # decide what real content to discard - so the top candidate is never
    # dropped silently, regardless of whether its own parent won the vote.
    top_candidate = top_n[0]
    kept_chunk_ids = set()
    if top_candidate["payload"].get("parent_chunk_id") not in covered_parent_ids:
        escalated_chunks.append({
            **top_candidate,
            "level": "child",
            "note": "kept: highest-scoring candidate in the full pool, retained even though "
                    "its parent topic didn't reach the majority-vote escalation threshold",
        })
        if top_candidate["payload"].get("chunk_id"):
            kept_chunk_ids.add(top_candidate["payload"]["chunk_id"])

    # Bug fix (2026-08-25, found via live testing against the SAME question
    # above, one position deeper): the fix just above only protects the
    # single #1 candidate - confirmed live this wasn't enough. Candidate #2
    # in that same pool (rerank_score +2.72, "Land Utilisation" topic - the
    # genuinely relevant content that used to be misplaced there by the
    # since-fixed page-4 column bug) was STILL silently dropped: it isn't
    # the #1 overall, and its own topic only got 1/10 votes, so it qualifies
    # for neither protection. It vanished while the "winning" Introduction
    # group - entirely scoring ~-9.7 to -11, essentially zero relevance -
    # took its place. Special-casing only position #1 was never going to
    # generalize to position #2, #3, etc. Generalizing this properly: ANY
    # candidate whose own score clears MEDIUM_THRESHOLD (already calibrated
    # for confidence tiering elsewhere in this file - reused, not a new
    # constant) is protected the same way #1 already was, regardless of its
    # rank or which parent it belongs to. Deliberately does NOT extend this
    # same guarantee to top_candidate itself (kept unconditional above,
    # regardless of its own score) - that guarantee predates this fix and
    # stays as-is, this only widens protection to cover strong matches
    # beyond just the single best one.
    for c in top_n[1:]:
        p = c["payload"]
        if p.get("parent_chunk_id") in covered_parent_ids:
            continue
        if c["rerank_score"] < MEDIUM_THRESHOLD:
            continue
        cid = p.get("chunk_id")
        if cid and cid in kept_chunk_ids:
            continue
        if cid:
            kept_chunk_ids.add(cid)
        escalated_chunks.append({
            **c,
            "level": "child",
            "note": f"kept: score {c['rerank_score']:.2f} clears the relevance floor "
                    f"(MEDIUM_THRESHOLD={MEDIUM_THRESHOLD}) even though its parent topic "
                    f"didn't win the majority-vote escalation threshold",
        })

    if not escalated_chunks:
        for c in top_n:
            c["level"] = "child"
        return {
            "escalated": False, "chunks": top_n, "child_candidates": top_n,
            "parent_vote_breakdown": parent_vote_breakdown, "escalation_threshold_count": threshold,
        }

    escalated_chunks.sort(key=lambda c: c["rerank_score"], reverse=True)
    return {
        "escalated": True,
        "dominant_parent_share": f"{parent_counts[qualifying_parent_ids[0]]}/{len(top_n)}",
        "escalated_parent_count": len(escalated_chunks),
        "chunks": escalated_chunks,
        "child_candidates": top_n,
        "parent_vote_breakdown": parent_vote_breakdown,
        "escalation_threshold_count": threshold,
    }


def retrieve(query: str, book_uuid: str, class_name: str = "", subject: str = "",
             chapter_id: Optional[str] = None, topic_id: Optional[str] = None) -> Dict:
    """
    `chapter_id`/`topic_id` are optional narrowing filters, per CTO spec
    section 9 ("use metadata filtering aggressively... before performing
    broad retrieval whenever the information is known with sufficient
    confidence"). STALE-COMMENT CORRECTION (2026-08-25): this used to say
    only the CLI test harness resolved book_uuid and no live caller passed
    chapter_id - that's no longer true. The live orchestrator
    (backend/app/orchestrator_test/test_runner.py::run_orchestrator_pipeline,
    called from chat.py) resolves both book_uuid and chapter_id and passes
    them through new_rag_adapter.hybrid_search_v2() into this function
    exactly as designed here, narrowing the search before it ever runs
    rather than filtering after the fact.
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
        "parent_vote_breakdown": escalation.get("parent_vote_breakdown", []),
        "escalation_threshold_count": escalation.get("escalation_threshold_count"),
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
