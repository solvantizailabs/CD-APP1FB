"""
Aggregate precision/recall/F1 across a batch of evaluate_case() results
(retrieval_eval.py) - the batch-level rollup metrics section 26-27 asks
for, separate from per-case detail.
"""
from typing import Dict, List


def aggregate_metrics(eval_results: List[Dict]) -> Dict:
    scored = [r for r in eval_results if not r.get("skipped")]
    if not scored:
        return {
            "case_count": 0, "skipped_count": len(eval_results),
            "avg_precision": None, "avg_recall": None, "avg_f1": None,
            "chapter_accuracy": None,
        }

    precisions = [r["precision"] for r in scored]
    recalls = [r["recall"] for r in scored]
    f1s = []
    for p, r in zip(precisions, recalls):
        f1s.append(2 * p * r / (p + r) if (p + r) > 0 else 0.0)

    chapter_correct = sum(1 for r in scored if r.get("correct_chapter"))

    return {
        "case_count": len(scored),
        "skipped_count": len(eval_results) - len(scored),
        "avg_precision": sum(precisions) / len(precisions),
        "avg_recall": sum(recalls) / len(recalls),
        "avg_f1": sum(f1s) / len(f1s),
        "chapter_accuracy": chapter_correct / len(scored),
    }
