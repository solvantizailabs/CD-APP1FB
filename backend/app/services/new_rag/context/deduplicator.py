"""
Context deduplication (CTO spec section 17): remove duplicate/near-identical
chunks, repeated definitions/examples, before reranking spends time scoring
them separately. Deliberately a cheap, local, no-API-call heuristic - a
second embedding-similarity pass would add cost and latency for a problem a
simple text-overlap check already solves for the actual failure mode this
exists to catch (near-identical chunk text from overlapping child-chunk
windows or duplicate topic coverage), not genuinely different phrasings of
the same idea.
"""
from typing import Dict, List

# Two chunks are treated as near-duplicates when this fraction of their
# words overlap (Jaccard similarity on word sets) - deliberately high so
# only genuinely repetitive content is dropped, never two chunks that
# happen to share common vocabulary but say different things.
DEFAULT_SIMILARITY_THRESHOLD = 0.85


def _word_set(text: str) -> set:
    return set(text.lower().split())


def _jaccard_similarity(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deduplicate(candidates: List[Dict], threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> List[Dict]:
    """
    Keeps the first (highest-ranked) occurrence of each near-duplicate
    group, drops the rest. Candidates must already be in ranked order
    (fusion score or rerank score) - the first-seen chunk in a duplicate
    group is assumed to be the best-ranked one, not re-scored here.
    """
    kept: List[Dict] = []
    kept_word_sets: List[set] = []

    for candidate in candidates:
        text = candidate.get("payload", {}).get("text", "")
        words = _word_set(text)
        is_duplicate = any(_jaccard_similarity(words, kept_words) >= threshold for kept_words in kept_word_sets)
        if not is_duplicate:
            kept.append(candidate)
            kept_word_sets.append(words)

    return kept
