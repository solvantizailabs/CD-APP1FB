"""
Standalone CLI runner tying retrieval_eval.py + precision_recall.py together
against the live new_rag retrieval path - the "take a report" deliverable
for Phase 1 (docs/implementation_phases.md's own stated requirement).

Didn't exist before this file: retrieval_eval.py only defined evaluate_case/
run_evaluation as importable functions, with no script actually calling them
against a real retrieve_fn. This fills that gap, nothing more.

test_dataset.json's cases span three different books (science/maths/social),
each with its own book_uuid - evaluate_case()/run_evaluation() take a single
book_uuid per call, so this groups cases by expected_subject and resolves
each group's book_uuid via the same lookup path (test_runner.py) the live
orchestrator uses, rather than hardcoding UUIDs here that could drift.

Usage: python -m backend.app.services.new_rag.evaluation.run_eval
"""
import json
import logging
import sys
from collections import defaultdict
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)


def _retrieve_fn(question: str, book_uuid: str, class_name: str = "", subject: str = "") -> Dict:
    """
    Adapts new_rag_adapter.hybrid_search_v2()'s (score, payload) tuple shape
    into the {"chunks": [{"payload": ..., "score": ...}]} shape evaluate_case()
    expects (same "chunks" key it already reads chunk_id off of via
    payload.chunk_id) - a pure shape adapter, no retrieval logic of its own.
    """
    from backend.app.services.retrieval import new_rag_adapter

    result = new_rag_adapter.hybrid_search_v2(
        query=question, book_uuid=book_uuid, class_name=class_name, subject=subject,
    )
    chunks = [
        {"payload": payload, "score": score}
        for score, payload in result.get("score_payload_pairs", [])
    ]
    return {
        "chunks": chunks,
        "status": result.get("status"),
        "confidence_tier": result.get("confidence_tier"),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from backend.app.orchestrator_test.test_runner import resolve_book_uuid_for_subject
    from backend.app.services.new_rag.evaluation.retrieval_eval import load_dataset, run_evaluation
    from backend.app.services.new_rag.evaluation.precision_recall import aggregate_metrics

    dataset = load_dataset()
    if not dataset:
        print("test_dataset.json is empty - nothing to evaluate.")
        return

    by_subject: Dict[str, List[Dict]] = defaultdict(list)
    for case in dataset:
        by_subject[(case.get("expected_class"), case.get("expected_subject"))].append(case)

    all_results: List[Dict] = []
    for (class_name, subject), cases in by_subject.items():
        try:
            grade = int(class_name)
        except (TypeError, ValueError):
            print(f"Skipping {len(cases)} case(s) - invalid expected_class {class_name!r}")
            continue

        book_uuid = resolve_book_uuid_for_subject(grade, subject)
        if not book_uuid:
            print(f"Skipping {len(cases)} case(s) - no ingested book found for "
                  f"class={class_name} subject={subject!r}")
            continue

        print(f"\n=== class={class_name} subject={subject} (book_uuid={book_uuid}) "
              f"- {len(cases)} case(s) ===")
        results = run_evaluation(cases, _retrieve_fn, book_uuid)
        for r in results:
            status = "SKIPPED" if r["skipped"] else f"P={r['precision']:.2f} R={r['recall']:.2f}"
            print(f"  [{status}] {r['question'][:70]}")
        all_results.extend(results)

    metrics = aggregate_metrics(all_results)
    print("\n=== Aggregate (this is the regression baseline) ===")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    sys.exit(main() or 0)
