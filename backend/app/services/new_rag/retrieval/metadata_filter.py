"""
Qdrant metadata-filter construction. Currently book_uuid only - the CTO
spec asks for aggressive filtering (class/subject/chapter) before broad
retrieval whenever that's known with sufficient confidence. Extending this
to chapter_id/topic_id is Phase 4 of docs/RAG_SPEC_ALIGNMENT_PLAN.md, gated
on the richer chunk metadata (Phase 1) actually existing on ingested data -
narrowing the filter here without that data existing yet would just filter
everything out.
"""
from typing import Optional

from qdrant_client import models


def build_filter(book_uuid: str, chapter_id: Optional[str] = None,
                  topic_id: Optional[str] = None) -> models.Filter:
    must = [models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
    if chapter_id:
        must.append(models.FieldCondition(key="chapter_id", match=models.MatchValue(value=chapter_id)))
    if topic_id:
        must.append(models.FieldCondition(key="topic_id", match=models.MatchValue(value=topic_id)))
    return models.Filter(must=must)
