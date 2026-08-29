"""
Semantic (dense-vector) half of hybrid retrieval. Builds the dense-vector
Qdrant prefetch clause; the actual combined query (fired alongside the
keyword prefetch) happens in hybrid_retriever.py, since Qdrant fuses dense +
sparse server-side in a single query_points call, not two separate
round-trips - this module's job is just constructing this half correctly.
"""
from typing import List

from openai import OpenAI
from qdrant_client import models

from backend.app.services.new_rag.embeddings.embedding_service import embed_dense

INITIAL_TOP_K = 20


def build_prefetch(openai_client: OpenAI, query: str, query_filter: models.Filter) -> models.Prefetch:
    dense_vec = embed_dense(openai_client, [query])[0]
    return models.Prefetch(query=dense_vec, using="dense", limit=INITIAL_TOP_K, filter=query_filter)
