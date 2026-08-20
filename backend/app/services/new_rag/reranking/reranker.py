"""
Cross-encoder reranking over fused hybrid-search candidates. See
docs/RAG_REDESIGN_PLAN.md, section 8.

Model choice (Xenova/ms-marco-MiniLM-L-6-v2) verified against current
external sources during the CTO-spec alignment pass: confirmed to be the
standard, widely-recommended lightweight cross-encoder for exactly this
use case (fast CPU inference, good accuracy/speed balance, no added LLM
token cost since it's a local model) - no change needed to the model
itself, only its location moved here per the spec's rag/reranking/ layout.
"""
from typing import Dict, List, Optional

from fastembed.rerank.cross_encoder import TextCrossEncoder

_reranker: Optional[TextCrossEncoder] = None


def _get_reranker() -> TextCrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = TextCrossEncoder(model_name="Xenova/ms-marco-MiniLM-L-6-v2")
    return _reranker


def rerank(query: str, candidates: List[Dict]) -> List[Dict]:
    """Ranks purely on text relevance today (query vs. chunk text). Folding in
    metadata-match signals (class/subject/chapter/topic/concept match, per
    the spec's fuller ranking-signal list) is planned once the richer chunk
    metadata schema exists - see docs/RAG_SPEC_ALIGNMENT_PLAN.md, Phase 5."""
    if not candidates:
        return []
    reranker = _get_reranker()
    texts = [c["payload"]["text"] for c in candidates]
    scores = list(reranker.rerank(query, texts))
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
    return candidates
