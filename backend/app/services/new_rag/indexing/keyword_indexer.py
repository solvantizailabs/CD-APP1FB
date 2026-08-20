"""
Keyword (lexical/BM25) indexing for the new RAG pipeline.

Locked decision (see docs/RAG_SPEC_ALIGNMENT_PLAN.md, section 1.1): this
uses Qdrant's native sparse vectors (Qdrant/bm25 via fastembed) rather than
PostgreSQL Full Text Search, which the CTO's spec names directly. Reason,
in full: there is no PostgreSQL anywhere in this codebase today (the app
uses Firestore for user data and Qdrant for vectors) - standing one up now
means new infrastructure (hosting, schema, a second write-path on every
ingestion run) for a requirement Qdrant-native sparse already functionally
satisfies: the same BM25 algorithm the spec references, running inside the
vector DB already in use, fused with dense search via RRF, with zero new
services. This also removes the old standalone BM25Okapi + local pickle
index's dependency on local disk entirely - confirmed real risk that
replaced: local disk does not survive a Render redeploy on this deployment
(git commit e1bc145).

Condition for revisiting: if real query testing shows exact scientific
terms, formulas, names, or dates are being missed that Postgres FTS would
plausibly catch, that's the trigger to add it - not a scheduled milestone.
"""
from typing import List

from fastembed import SparseTextEmbedding

_sparse_model: SparseTextEmbedding = None


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _sparse_model


def embed_sparse(texts: List[str]):
    return list(_get_sparse_model().embed(texts))
