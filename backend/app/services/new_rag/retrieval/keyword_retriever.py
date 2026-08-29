"""
Keyword (sparse-vector/BM25) half of hybrid retrieval. Builds the
sparse-vector Qdrant prefetch clause; see semantic_retriever.py's docstring
for why the actual combined query happens in hybrid_retriever.py instead of
here.
"""
from qdrant_client import models

from backend.app.services.new_rag.indexing.keyword_indexer import embed_sparse

INITIAL_TOP_K = 20


def build_prefetch(query: str, query_filter: models.Filter) -> models.Prefetch:
    sparse_vec = list(embed_sparse([query]))[0]
    return models.Prefetch(
        query=models.SparseVector(indices=sparse_vec.indices.tolist(), values=sparse_vec.values.tolist()),
        using="sparse", limit=INITIAL_TOP_K, filter=query_filter,
    )
