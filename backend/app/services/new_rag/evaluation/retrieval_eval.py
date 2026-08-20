"""
Evaluation harness for the new RAG pipeline (CTO spec section 26-27).

`test_dataset.json` starts empty deliberately, not seeded with fabricated
example entries - every field in a real entry (expected_chapter,
expected_page, expected_relevant_chunks, ...) is a ground-truth claim about
a real ingested book, and right now nothing is ingested (see
docs/RAG_SPEC_ALIGNMENT_PLAN.md - the Qdrant collection and local outputs
were deliberately cleared before this implementation phase). Filling this
in with real, verified cases is the user's job once a real book is ingested
and real questions are tested against it - this harness's job is to be
ready to run that dataset the moment it exists, not to invent placeholder
"expected" answers nobody has verified.

Each dataset entry is expected to have: question, expected_class,
expected_subject, expected_chapter, expected_topic, expected_concept,
expected_source, expected_page, expected_relevant_chunks (list of chunk_ids),
expected_answer_points (list of strings) - per spec section 26's own field
list.
"""
import json
import logging
import os
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATASET_PATH = os.path.join(os.path.dirname(__file__), "test_dataset.json")


def load_dataset(path: Optional[str] = None) -> List[Dict]:
    path = path or _DATASET_PATH
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_case(case: Dict, retrieve_fn: Callable[..., Dict], book_uuid: str) -> Dict:
    """
    Runs one dataset case through `retrieve_fn` and checks the returned
    chunks against `expected_relevant_chunks`. A case with no
    expected_relevant_chunks recorded yet is skipped (not failed) - an
    unfilled expectation isn't a wrong answer, it's missing ground truth.
    """
    question = case["question"]
    expected_chunks = set(case.get("expected_relevant_chunks", []))

    if not expected_chunks:
        return {"question": question, "skipped": True, "reason": "no expected_relevant_chunks recorded yet"}

    result = retrieve_fn(question, book_uuid,
                          class_name=case.get("expected_class", ""),
                          subject=case.get("expected_subject", ""))
    chunks = result.get("chunks", result.get("best_attempt_chunks", []))
    returned_ids = {c.get("payload", {}).get("chunk_id") for c in chunks}

    true_positives = returned_ids & expected_chunks
    precision = len(true_positives) / len(returned_ids) if returned_ids else 0.0
    recall = len(true_positives) / len(expected_chunks) if expected_chunks else 0.0

    return {
        "question": question,
        "skipped": False,
        "status": result.get("status"),
        "confidence_tier": result.get("confidence_tier"),
        "expected_chunk_count": len(expected_chunks),
        "returned_chunk_count": len(returned_ids),
        "true_positive_count": len(true_positives),
        "precision": precision,
        "recall": recall,
        "correct_chapter": result.get("chunks", [{}])[0].get("payload", {}).get("chapter_name") == case.get("expected_chapter")
                            if chunks else False,
    }


def run_evaluation(dataset: List[Dict], retrieve_fn: Callable[..., Dict], book_uuid: str) -> List[Dict]:
    results = [evaluate_case(case, retrieve_fn, book_uuid) for case in dataset]
    n_run = sum(1 for r in results if not r["skipped"])
    n_skipped = len(results) - n_run
    logger.info(f"[NEW_RAG][Evaluation] Ran {n_run} case(s), skipped {n_skipped} (no ground truth recorded yet).")
    return results
