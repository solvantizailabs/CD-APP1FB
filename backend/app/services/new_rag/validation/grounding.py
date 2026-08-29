"""
Grounding validation (CTO spec sections 2 and 27): checks that a generated
answer is actually supported by the retrieved context, catching fabricated
("hallucinated") claims before they reach a student.

`new_rag` doesn't generate answers itself - by design, this layer returns
retrieved context, generation happens downstream (the orchestrator/LLM
layer, see docs/RAG_SPEC_ALIGNMENT_PLAN.md section 1.2). This module is the
reusable check that layer should call with (context, generated_answer).

Deliberately a lexical word-overlap heuristic, not an LLM-based entailment
check - same "local, no added API cost" reasoning as deduplicator.py and
compressor.py. This is a real first-pass check, not a claim of true
semantic entailment - a known limitation, honestly documented rather than
overstated, with room to swap in a proper NLI/entailment model later if
real failures show this heuristic isn't catching enough (the same
validate-before-over-engineering pattern used throughout this project).
"""
import re
from typing import Dict, List

DEFAULT_MIN_OVERLAP_RATIO = 0.3


def _sentence_split(text: str) -> List[str]:
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def check_grounding(context: str, answer: str, min_overlap_ratio: float = DEFAULT_MIN_OVERLAP_RATIO) -> Dict:
    """
    For each sentence in `answer`, checks what fraction of its words also
    appear in `context`. A sentence below `min_overlap_ratio` is flagged as
    unsupported. Returns is_grounded=True only when every sentence clears
    the bar - one fabricated sentence in an otherwise-supported answer is
    still a grounding failure, not averaged away.
    """
    context_words = set((context or "").lower().split())
    sentences = _sentence_split(answer)

    if not sentences:
        return {"is_grounded": True, "overlap_ratio": 1.0, "unsupported_sentences": [], "checked_sentences": 0}

    unsupported: List[str] = []
    ratios: List[float] = []
    for sentence in sentences:
        words = set(sentence.lower().split())
        if not words:
            continue
        overlap = len(words & context_words) / len(words)
        ratios.append(overlap)
        if overlap < min_overlap_ratio:
            unsupported.append(sentence)

    avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
    return {
        "is_grounded": len(unsupported) == 0,
        "overlap_ratio": avg_ratio,
        "unsupported_sentences": unsupported,
        "checked_sentences": len(sentences),
    }
