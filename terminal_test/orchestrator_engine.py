"""
Terminal test harness for the new orchestrator engine
(backend/app/services/question_pipeline/), per
docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md section 8.

Login via student email only - GET /api/students/lookup, the same real,
already-registered-student lookup terminal_test/answer_generation.py uses.
NO subject/book picker: the new engine resolves subject itself at Stage 2,
so this harness calls it the same way a real student's request would
arrive - email + question, nothing else.

WHY THIS IS AN IN-PROCESS CALL, NOT AN HTTP CALL (the one deliberate
exception to the "endpoint-only" pattern the other terminal_test/ scripts
follow): the new engine is NOT YET wired into any live endpoint
(pipeline.py's own docstring: deliberately not cut over into
/api/smart_query yet, per the project's no-live-cutover-without-validation
constraint). There is no endpoint to call. This is NOT mocking - every
call inside pipeline.run_pipeline() is a real call against real
infrastructure (real OpenAI, real Qdrant, real Firestore, real Redis
session) - it's simply invoked as a direct Python call instead of over
HTTP, because no HTTP surface for it exists yet. Once cutover happens,
this script's login step stays the same but the middle step becomes a real
HTTP call, same as answer_generation.py.

Usage:
    python terminal_test/orchestrator_engine.py
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import httpx

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "orchestrator_engine")


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "question"


def prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        return ""


def lookup_student(base_url: str, email: str) -> dict:
    resp = httpx.get(f"{base_url}/api/students/lookup", params={"email": email}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def print_step_log(result, trace_extra: dict) -> None:
    print("-" * 70)
    for line in result.trace:
        tag = line.split("=", 1)[0].split(" ", 1)[0].upper()
        print(f"[{tag}] {line}")
    if result.rag_result and result.rag_result.sources:
        print(f"[CHUNKS_USED] {len(result.rag_result.sources)} chunk(s), status={result.rag_result.retrieval_status}")
        for c in result.rag_result.sources:
            print(f"    - {c.get('chapter')} | {c.get('topic')} | p.{c.get('page')} | score={c.get('score')}")
    print("-" * 70)


def to_json_record(email, uid, class_name, board, language, session_id, question, result) -> dict:
    safety_chain = []
    if result.safety:
        safety_chain.append(result.safety)

    curriculum_decision = result.curriculum_decision
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "email": email,
        "uid": uid,
        "class_name": class_name,
        "board": board,
        "language": language,
        "raw_question": question,
        "status": result.status,
        "safety": {
            "layer1_rules": {
                "jailbreak_detected": result.safety.layer1_jailbreak_detected if result.safety else None,
                "academic_integrity_detected": result.safety.layer1_academic_integrity_detected if result.safety else None,
                "default_allow_followup": result.safety.layer1_default_allowed_followup if result.safety else None,
            },
            "layer2_moderation": {
                "categories_flagged": result.safety.layer2_categories_flagged if result.safety else [],
                "borderline": result.safety.layer2_borderline if result.safety else None,
            },
            "layer3_residual_recheck": {
                "ran": result.safety.layer3_ran if result.safety else False,
                "result": result.safety.layer3_result if result.safety else None,
            },
        },
        "reformulated": {
            "standalone_question": result.query.resolved_question if result.query else None,
            "continuity": result.routing.route if result.routing else None,
            "subject": result.curriculum_guess.subject if result.curriculum_guess else None,
            "chapter": result.curriculum_guess.chapter if result.curriculum_guess else None,
            "topic": result.curriculum_guess.topic if result.curriculum_guess else None,
        },
        "curriculum_decision": {
            "curriculum_match": curriculum_decision.curriculum_match if curriculum_decision else None,
            "decided_at_stage": curriculum_decision.decided_at_stage if curriculum_decision else None,
            "confidence_tier": curriculum_decision.rag.confidence_tier if (curriculum_decision and curriculum_decision.rag) else None,
        },
        "session_window": {
            "session_id": session_id,
            "ttl_seconds": 86400,
        },
        "retrieval": {
            # result.rag_result is Stage 5's ACTUAL fetch result used for
            # generation - NOT curriculum_decision.rag (Stage 4's raw
            # search). Real bug fixed 2026-09-01: on a follow-up that
            # reuses cached chunks, these two differ - this field used to
            # read curriculum_decision.rag here, which silently showed the
            # wrong (stale, Stage-4-only) chunks whenever a cache reuse
            # happened.
            "confidence_tier": result.rag_result.confidence_tier if result.rag_result else None,
            "retrieval_status": result.rag_result.retrieval_status if result.rag_result else None,
            "chunk_count": len(result.rag_result.sources) if result.rag_result else 0,
            "chunks": result.rag_result.sources if result.rag_result else [],
        },
        "format_decision": result.format_decision,
        "answer": {
            "text_narration": result.final_answer if result.format_decision != "VIDEO_REQUIRED" else None,
        },
        "trace": result.trace,
    }


def login(base_url: str, email: str) -> dict:
    """
    Tries the real HTTP endpoint first (matches a live-server test run).
    Falls back to a direct Firestore lookup (test_runner.py's
    authenticate_student_by_email, which itself falls back to a seeded test
    profile if the email isn't found) when no server is reachable - this is
    NOT mocking, it's the same real Firestore call the HTTP endpoint itself
    would make, just invoked in-process because no server is up right now.
    """
    try:
        student = lookup_student(base_url, email)
        if student.get("found"):
            return student
        print(f"[LOGIN] No registered student found via HTTP for {email!r}.")
    except Exception:
        print(f"[LOGIN] No server reachable at {base_url} - falling back to a direct Firestore lookup.")

    from backend.app.orchestrator_test.test_runner import authenticate_student_by_email
    profile = authenticate_student_by_email(email)
    return {
        "found": True,
        "uid": profile["uid"],
        "name": profile["name"],
        "class": profile["class"],
        "board": profile["board"],
    }


def run_batch(email: str, base_url: str, model: str, questions: list) -> None:
    student = login(base_url, email)
    uid = student["uid"]
    class_name = str(student["class"])
    board = student.get("board") or "CBSE"
    language = "English"
    print(f"Logged in as: {student.get('name') or email} (Class {class_name}, {board}, uid={uid})")

    from backend.app.services.llm.openai_client import create_client
    from backend.app.services.chat.session_service import session_manager
    from backend.app.services.question_pipeline.pipeline import run_pipeline
    from backend.app.services.question_pipeline.schemas import PipelineInput

    openai_client = create_client()
    session = session_manager.get_or_create_session(book_uuid=f"orchestrator_engine_test_{uid}")
    session_id = session["session_id"]

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = []

    for i, question in enumerate(questions, start=1):
        print("\n" + "#" * 70)
        print(f"# QUESTION {i}/{len(questions)}: {question}")
        print("#" * 70)

        conversation_context = session_manager.get_window(session_id)
        pipeline_input = PipelineInput(
            student_question=question,
            conversation_context=conversation_context,
            learner_context={"class_name": class_name, "board": board, "language": language},
            session_context={},
            session_id=session_id,
            uid=uid,
        )

        result = run_pipeline(pipeline_input, openai_client, model)
        print_step_log(result, {})
        print(f"\nSTATUS: {result.status}")
        print(f"ANSWER:\n{result.final_answer}")

        session_manager.add_turn(session_id, {"query": question, "answer": result.final_answer})

        record = to_json_record(email, uid, class_name, board, language, session_id, question, result)
        out_path = os.path.join(OUTPUTS_DIR, f"{run_timestamp}_{i:02d}_{_slug(question)}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        print(f"[SAVED] {out_path}")

        summary.append({
            "n": i, "question": question, "status": result.status,
            "curriculum_match": result.curriculum_decision.curriculum_match if result.curriculum_decision else None,
            "format_decision": result.format_decision, "output_file": out_path,
        })

    summary_path = os.path.join(OUTPUTS_DIR, f"{run_timestamp}_00_SUMMARY.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\n" + "=" * 70)
    print(f"BATCH COMPLETE - {len(questions)} questions. Summary: {summary_path}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Application base URL (for student lookup only)")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--email", default=None, help="Student email (skips the interactive prompt)")
    parser.add_argument("--questions-file", default=None, help="Path to a text file, one question per line - runs all of them in one session, non-interactively")
    args = parser.parse_args()

    print("=" * 70)
    print("ORCHESTRATOR ENGINE TEST (new question_pipeline/, not yet live)")
    print("=" * 70)

    email = args.email or prompt("\nEnter your student email address: ")
    if not email:
        print("An email is required for this tool - exiting.")
        sys.exit(1)

    if args.questions_file:
        with open(args.questions_file, "r", encoding="utf-8") as f:
            questions = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        if not questions:
            print(f"No questions found in {args.questions_file} - exiting.")
            sys.exit(1)
        run_batch(email, args.base_url, args.model, questions)
        return

    # Interactive mode (original behavior, unchanged)
    student = login(args.base_url, email)
    uid = student["uid"]
    class_name = str(student["class"])
    board = student.get("board") or "CBSE"
    language = "English"
    print(f"Welcome, {student.get('name') or email} (Class {class_name}, {board}, uid={uid})")

    from backend.app.services.llm.openai_client import create_client
    from backend.app.services.chat.session_service import session_manager
    from backend.app.services.question_pipeline.pipeline import run_pipeline
    from backend.app.services.question_pipeline.schemas import PipelineInput

    openai_client = create_client()
    session = session_manager.get_or_create_session(book_uuid=f"orchestrator_engine_test_{uid}")
    session_id = session["session_id"]

    while True:
        question = prompt("\nAsk a question (or press Enter to quit): ")
        if not question:
            print("Bye.")
            break

        conversation_context = session_manager.get_window(session_id)

        pipeline_input = PipelineInput(
            student_question=question,
            conversation_context=conversation_context,
            learner_context={"class_name": class_name, "board": board, "language": language},
            session_context={},
            session_id=session_id,
            uid=uid,
        )

        result = run_pipeline(pipeline_input, openai_client, args.model)

        print_step_log(result, {})
        print(f"\nSTATUS: {result.status}")
        print(f"ANSWER:\n{result.final_answer}")

        session_manager.add_turn(session_id, {"query": question, "answer": result.final_answer})

        record = to_json_record(email, uid, class_name, board, language, session_id, question, result)
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = os.path.join(OUTPUTS_DIR, f"{timestamp}_{_slug(question)}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        print(f"[SAVED] {out_path}")


if __name__ == "__main__":
    main()
