"""
The RAG context package contract (CTO spec section 19) - the structured
shape the RAG layer hands to the LLM, so "the LLM should never need to know
how Qdrant or PostgreSQL works" (the spec's own framing). Defined as a
TypedDict for shape-checking, not a runtime-enforced class - this project's
existing code (retrieve()'s current dict return) already works this way,
kept consistent rather than introducing a second validation pattern.
"""
from typing import Dict, List, Optional, TypedDict


class SourceEntry(TypedDict):
    chunk_id: str
    source: str
    page: Optional[int]
    relevance_score: float
    content: str


class Curriculum(TypedDict):
    class_: str  # "class" is a Python keyword, spec's field name kept in the dict output instead
    subject: str
    chapter: Optional[str]
    topic: Optional[str]
    concept: Optional[str]


class RagContextPackage(TypedDict):
    query: str
    resolved_query: str
    intent: Optional[str]
    curriculum: Dict  # spec's Curriculum shape, built with literal "class" key (see context_builder.py)
    sources: List[SourceEntry]
    context: str
    confidence: str
    retrieval_status: str


REQUIRED_KEYS = ("query", "resolved_query", "intent", "curriculum", "sources",
                  "context", "confidence", "retrieval_status")


def is_valid_package(package: Dict) -> bool:
    """Structural check only (all required top-level keys present) - not a
    claim of content correctness, just shape conformance to the contract."""
    return all(key in package for key in REQUIRED_KEYS)
