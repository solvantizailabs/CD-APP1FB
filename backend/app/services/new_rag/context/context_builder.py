"""
Assembles retrieve()'s result (already deduped, dynamically-sized,
confidence-scored, and compressed) into the CTO spec's exact RAG context
package contract (section 19, see schemas/rag_response.py).

`resolved_query`/`intent`/`curriculum` (chapter/topic/concept) are accepted
as optional parameters rather than computed here - resolving those is the
orchestrator's job today (docs/RAG_SPEC_ALIGNMENT_PLAN.md, section 1.2).
When not provided, `resolved_query` honestly falls back to the raw query
(no fabricated reformulation) and `intent`/`chapter`/`topic`/`concept` stay
None rather than guessed.
"""
from typing import Dict, List, Optional


def build_context_package(query: str, retrieve_result: Dict, class_name: str = "", subject: str = "",
                           resolved_query: Optional[str] = None, intent: Optional[str] = None,
                           chapter: Optional[str] = None, topic: Optional[str] = None,
                           concept: Optional[str] = None) -> Dict:
    chunks = retrieve_result.get("chunks", retrieve_result.get("best_attempt_chunks", []))

    sources: List[Dict] = []
    context_parts: List[str] = []
    for c in chunks:
        payload = c.get("payload", {})
        content = payload.get("compressed_text") or payload.get("text", "")
        sources.append({
            "chunk_id": payload.get("chunk_id") or payload.get("parent_chunk_id", ""),
            "source": payload.get("document_name") or payload.get("chapter_name", ""),
            "page": payload.get("start_page"),
            "relevance_score": c.get("rerank_score", 0.0),
            "content": content,
        })
        context_parts.append(content)

    return {
        "query": query,
        "resolved_query": resolved_query or query,
        "intent": intent,
        "curriculum": {
            "class": class_name,
            "subject": subject,
            "chapter": chapter,
            "topic": topic,
            "concept": concept,
        },
        "sources": sources,
        "context": "\n\n".join(part for part in context_parts if part),
        "confidence": retrieve_result.get("confidence_tier", "INSUFFICIENT"),
        "retrieval_status": retrieve_result.get("status", "insufficient_context"),
    }
