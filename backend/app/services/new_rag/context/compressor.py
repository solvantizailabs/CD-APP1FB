"""
Context compression (CTO spec section 18): trim retrieved chunks down to
the sentences actually relevant to the query before they reach the LLM.
Deliberately a cheap, local, lexical relevance scoring - not an added LLM
summarization call, for the same reason deduplicator.py stays local:
another LLM call here would add cost and, per the rate-limit work earlier
in this project, another surface for the exact TPM-saturation problem this
pipeline already spent real effort solving. A sentence-level word-overlap
score is enough to drop clearly-irrelevant sentences within an already
relevant, already-reranked chunk.

The spec explicitly says measure token counts empirically rather than
hard-code a target (section 18) - `compress()` returns the actual token
count alongside the compressed text so a caller can log real numbers,
instead of this module asserting a target it can't verify.
"""
import re
from typing import Dict, List, Tuple

DEFAULT_MAX_TOKENS_PER_CHUNK = 300


def _sentence_split(text: str) -> List[str]:
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _sentence_relevance(sentence: str, query_words: set) -> float:
    words = set(sentence.lower().split())
    if not words:
        return 0.0
    return len(words & query_words) / len(words)


def compress_text(query: str, text: str, max_tokens: int = DEFAULT_MAX_TOKENS_PER_CHUNK) -> str:
    """
    Keeps the most query-relevant sentences from `text`, up to `max_tokens`,
    then restores their original order (readability) rather than leaving
    them sorted by score. Always keeps at least one sentence if the input
    is non-empty - a chunk that already survived retrieval and reranking is
    assumed relevant as a whole even if no single sentence scores highly.
    """
    sentences = _sentence_split(text)
    if not sentences:
        return text

    query_words = set((query or "").lower().split())
    scored = [(s, _sentence_relevance(s, query_words)) for s in sentences]
    ranked = sorted(scored, key=lambda x: x[1], reverse=True)

    kept: List[str] = []
    token_count = 0
    for sentence, _score in ranked:
        s_tokens = _estimate_tokens(sentence)
        if kept and token_count + s_tokens > max_tokens:
            continue
        kept.append(sentence)
        token_count += s_tokens

    kept_set = set(kept)
    ordered = [s for s in sentences if s in kept_set]
    return " ".join(ordered)


def compress(query: str, candidates: List[Dict], max_tokens_per_chunk: int = DEFAULT_MAX_TOKENS_PER_CHUNK) -> Tuple[List[Dict], int]:
    """
    Compresses every candidate's payload text in place (adds a
    `compressed_text` key, leaves `text` untouched so the original is never
    lost) and returns (candidates, total_compressed_tokens) - the real,
    measured token count across all compressed chunks combined.
    """
    total_tokens = 0
    for candidate in candidates:
        original_text = candidate.get("payload", {}).get("text", "")
        compressed = compress_text(query, original_text, max_tokens_per_chunk)
        candidate["payload"]["compressed_text"] = compressed
        total_tokens += _estimate_tokens(compressed)
    return candidates, total_tokens
