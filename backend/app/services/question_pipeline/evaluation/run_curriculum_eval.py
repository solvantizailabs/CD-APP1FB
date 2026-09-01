"""
Runs curriculum_match_eval.json through the REAL Stage 2 (understanding) +
Stage 4 (rag_stage.decide_curriculum) pipeline - real OpenAI calls, real
Qdrant retrieval, no mocking, same "no mocking, ever" convention as
terminal_test/. This is the empirical eval set the research (2026-08-30
session) recommended: build labeled cases, run the pipeline, compare
against ground truth, THEN decide whether the confidence-tier thresholds
in new_rag/retrieval/hybrid_retriever.py (HIGH=0.0/MEDIUM=-5.0/LOW=-8.0)
actually need retuning - not guessed, evidenced.

Deliberately isolates Stage 2 + Stage 4 only (no safety layers, no
generation) - this eval is about the curriculum_match decision, not the
whole pipeline.

Usage:
    python -m backend.app.services.question_pipeline.evaluation.run_curriculum_eval
"""
import json
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(override=True)

DATASET_PATH = os.path.join(os.path.dirname(__file__), "curriculum_match_eval.json")
OUTPUTS_DIR = os.path.join(os.path.dirname(__file__), "outputs")

# How close to a tier boundary counts as "boundary-sensitive" - a case whose
# raw score lands within this margin of HIGH/MEDIUM/LOW is exactly the kind
# of evidence that should inform whether the threshold needs to move, not
# just whether the current threshold happens to get it right today.
BOUNDARY_MARGIN = 1.0


def run():
    from backend.app.services.llm.openai_client import create_client
    from backend.app.services.question_pipeline import understanding, rag_stage
    from backend.app.services.new_rag.retrieval.hybrid_retriever import HIGH_THRESHOLD, MEDIUM_THRESHOLD, LOW_THRESHOLD

    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    grade = dataset["grade"]
    client = create_client()
    learner_context = {"class_name": str(grade)}

    results = []
    for case in dataset["cases"]:
        question = case["question"]
        expected = case["expected_curriculum_match"]

        raw = understanding.understand_question(question, [], learner_context, client, "gpt-4o-mini")
        curriculum_guess = understanding.to_curriculum_guess(raw, grade)
        query = understanding.to_reformulated_query(raw, question, raw.get("resolved_question") or question)

        decision = rag_stage.decide_curriculum(curriculum_guess, grade, query=query)
        actual = decision.curriculum_match
        top_score = decision.rag.confidence if decision.rag else None
        tier = decision.rag.confidence_tier if decision.rag else None

        boundary_sensitive = False
        if top_score is not None:
            for boundary in (HIGH_THRESHOLD, MEDIUM_THRESHOLD, LOW_THRESHOLD):
                if abs(top_score - boundary) <= BOUNDARY_MARGIN:
                    boundary_sensitive = True

        result = {
            "id": case["id"],
            "question": question,
            "category": case.get("category"),
            "expected": expected,
            "actual": actual,
            "correct": actual == expected,
            "guessed_subject": curriculum_guess.subject,
            "guessed_chapter": curriculum_guess.chapter,
            "expected_subject": case.get("expected_subject"),
            "confidence_tier": tier,
            "top_score": top_score,
            "boundary_sensitive": boundary_sensitive,
        }
        results.append(result)
        status = "PASS" if result["correct"] else "FAIL"
        print(f"[{status}] {case['id']:20} expected={expected:16} actual={str(actual):16} tier={tier} score={top_score} | {question[:55]}")

    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    in_grade_expected = [r for r in results if r["expected"] == "in_grade"]
    in_grade_correct = sum(1 for r in in_grade_expected if r["correct"])
    not_curr_expected = [r for r in results if r["expected"] == "not_in_curriculum"]
    not_curr_correct = sum(1 for r in not_curr_expected if r["correct"])
    boundary_cases = [r for r in results if r["boundary_sensitive"]]

    print("\n" + "=" * 70)
    print(f"OVERALL ACCURACY: {correct}/{total} ({100*correct/total:.1f}%)")
    print(f"  in_grade cases:         {in_grade_correct}/{len(in_grade_expected)} correct")
    print(f"  not_in_curriculum cases: {not_curr_correct}/{len(not_curr_expected)} correct")
    print(f"  Boundary-sensitive cases (within {BOUNDARY_MARGIN} of a threshold): {len(boundary_cases)}")
    for r in boundary_cases:
        print(f"    - {r['id']}: score={r['top_score']}, tier={r['confidence_tier']}, correct={r['correct']} | {r['question'][:50]}")

    misses = [r for r in results if not r["correct"]]
    if misses:
        print(f"\nMISCLASSIFIED ({len(misses)}):")
        for r in misses:
            print(f"  - {r['id']}: expected={r['expected']} got={r['actual']} (score={r['top_score']}, tier={r['confidence_tier']}) | {r['question']}")

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(OUTPUTS_DIR, f"{timestamp}_curriculum_eval.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "total": total, "correct": correct, "accuracy": correct / total,
            "in_grade_accuracy": in_grade_correct / len(in_grade_expected) if in_grade_expected else None,
            "not_in_curriculum_accuracy": not_curr_correct / len(not_curr_expected) if not_curr_expected else None,
            "current_thresholds": {"HIGH": HIGH_THRESHOLD, "MEDIUM": MEDIUM_THRESHOLD, "LOW": LOW_THRESHOLD},
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n[SAVED] {out_path}")


if __name__ == "__main__":
    run()
