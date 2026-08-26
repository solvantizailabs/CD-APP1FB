"""
Terminal answer-generation test - replicates the REAL user.html flow end to
end: log in with a student email, ask a question, get the actual generated
answer - through the real /api/smart_query endpoint, the exact same one
the browser UI calls (personalization, orchestrator classification, RAG
retrieval, grounding, all of it - nothing simulated).

Deliberately has ZERO imports from `backend` - only ever talks to the
running application over HTTP:

    GET /api/students/lookup - resolve the student's uid + class from their email
    GET /api/subjects        - list subjects available for that class
    GET /api/books/all       - filter down to subjects that actually have content
    GET /api/smart_query     - THE real answer endpoint (SSE stream) - orchestrator
                                classification, personalization, RAG retrieval,
                                grounding, generation - unchanged from what the
                                browser calls
    GET /api/retrieve        - re-run retrieval for the same question, only to
                                show what fed the answer (see note below)

Same design principle as ingestion.py/retrieval.py: as the orchestrator and
personalized-learning layers get rebuilt (10 steps, 20 steps, however many),
this script keeps working unmodified as long as /api/smart_query's contract
holds - it is the actual integration test for that whole layer, not a
simulation of it.

WHY LOGIN IS MANDATORY HERE (unlike retrieval.py): this script exists to
test the real, personalized, per-student experience - class resolution,
per-student memory, escalation, quadrant - all of which require a real uid.
There is no anonymous/skip path, by design.

WHY NO BOOK-BROWSING LIST: the real UI doesn't show a book picker either -
a logged-in student's class determines what's available, and the question
itself is what the orchestrator uses to resolve subject/chapter. This
script auto-selects the subject when the student's class only has one
ingested book (true for all current test data) and only prompts when a
class genuinely has more than one, since /api/smart_query still requires an
explicit class_name/subject/book_uuid per call - there is no
"figure out the book from the question alone" endpoint to call instead.

WHY /api/retrieve IS ALSO CALLED: /api/smart_query's SSE stream never sends
the full retrieved-chunk list to the browser - that detail is written to a
per-query debug JSON on Supabase instead, linked from a Firestore
`user_queries` doc, not surfaced over SSE at all. Re-running retrieval.py's
same endpoint with the identical question (after the real answer is in
hand) is more robust than trying to reconstruct/fetch that Supabase record
directly (which would couple this script to Firestore/Supabase document
shapes instead of a stable endpoint), and gives the same clean chunk view
retrieval.py already provides - this is genuinely what retrieval found for
this question, not a simulation of it.

Uses the existing "mock-token-{uid}" dev auth bypass already built into
auth_middleware.py (see test_personalization_cli.py for the same pattern)
instead of a real Firebase sign-in - this script identifies a real,
already-registered student by email/uid, it does not perform password
authentication.

Usage:
    python terminal_test/answer_generation.py
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "answer_generation")


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "query"


def save_output(email: str, class_name: str, subject: str, session_id: str,
                 question: str, answer_result: dict, retrieval_result: dict) -> str:
    """
    Saves this turn's request + real generated answer + orchestrator routing
    + the retrieval detail that fed it, as JSON under outputs/answer_generation/
    - a versioned record of a real end-to-end run (the same one
    show_retrieval_detail() prints), so a specific turn's full result can be
    diffed against a later run without relying on terminal scrollback.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}_{_slug(question)}.json"
    path = os.path.join(OUTPUTS_DIR, filename)
    payload = {
        "timestamp": timestamp,
        "email": email,
        "class_name": class_name,
        "subject": subject,
        "session_id": session_id,
        "question": question,
        "intent": answer_result.get("intent"),
        "answer": answer_result.get("answer"),
        "retrieval": retrieval_result,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        return ""


def lookup_student(base_url: str, email: str) -> dict:
    resp = httpx.get(f"{base_url}/api/students/lookup", params={"email": email}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def resolve_subject(base_url: str, class_name: str) -> str:
    """
    Mirrors retrieval.py's list_books_for_class(), kept standalone here
    rather than imported - these scripts are deliberately independent, no
    shared internal module, so either can be copied/run on its own.
    """
    subj_resp = httpx.get(f"{base_url}/api/subjects", params={"class_name": class_name}, timeout=15)
    subj_resp.raise_for_status()
    configured = [s.get("name") for s in subj_resp.json().get("subjects", []) if s.get("name")]

    books_resp = httpx.get(f"{base_url}/api/books/all", timeout=15)
    books_resp.raise_for_status()
    ingested = {b["subject"].lower() for b in books_resp.json().get("books", []) if b["class_name"] == str(class_name)}

    available = [s for s in configured if s.lower() in ingested]
    if not available:
        print(f"No ingested books found for Class {class_name}.")
        sys.exit(1)
    if len(available) == 1:
        print(f"Subject (auto-selected, only one available for Class {class_name}): {available[0]}")
        return available[0]

    print(f"\nClass {class_name} has multiple subjects available:")
    for i, s in enumerate(available, start=1):
        print(f"  {i}. {s}")
    while True:
        choice = prompt(f"Select a subject [1-{len(available)}]: ")
        if choice.isdigit() and 1 <= int(choice) <= len(available):
            return available[int(choice) - 1]
        print("Invalid choice, try again.")


def ask_smart_query(base_url: str, uid: str, query: str, class_name: str, subject: str,
                     book_uuid: str, session_id: str) -> dict:
    """
    Streams the real /api/smart_query SSE response and reassembles it into
    one result: the full answer text (concatenated display_text chunks,
    exactly as the browser would render them one after another), the
    orchestrator's own classification/routing decision (the 'intent'
    event), and the session_id to carry into the next turn (real
    conversational continuity, same as the browser).
    """
    params = {
        "book_uuid": book_uuid,
        "query": query,
        "class_name": class_name,
        "subject": subject,
        "token": f"mock-token-{uid}",
    }
    if session_id:
        params["session_id"] = session_id

    answer_parts = []
    intent = {}
    new_session_id = session_id

    with httpx.stream("GET", f"{base_url}/api/smart_query", params=params, timeout=180) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type == "session":
                new_session_id = event.get("session_id")
            elif event_type == "intent":
                intent = event
            elif event_type in ("query_id", "progress", "lesson_ready", "all_scene_audio_ready"):
                continue  # video/lesson-mode bookkeeping, not relevant to a text answer
            elif "display_text" in event:
                answer_parts.append(event["display_text"])
            elif "error" in event:
                print(f"\n[ERROR from smart_query] {event['error']}")

    return {
        "answer": "".join(answer_parts).strip(),
        "intent": intent,
        "session_id": new_session_id,
    }


def show_retrieval_detail(base_url: str, query: str, class_name: str, subject: str) -> dict:
    """
    See module docstring's "WHY /api/retrieve IS ALSO CALLED" - this is a
    second, separate call, not something smart_query's stream provides.
    """
    resp = httpx.get(
        f"{base_url}/api/retrieve",
        params={"query": query, "class_name": class_name, "subject": subject},
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()

    print(f"\n{'-'*70}")
    print(f"RETRIEVAL DETAIL (what fed this answer - {result.get('chunk_count')} chunk(s), "
          f"confidence={result.get('confidence_tier')}, escalated={result.get('escalated_to_parent')})")
    print(f"{'-'*70}")
    for i, c in enumerate(result.get("chunks", []), start=1):
        chunk_type = c.get("chunk_type", "?")
        topic = c.get("topic_name", "?")
        if chunk_type == "diagram":
            print(f"  [{i}] diagram | {topic!r} | image: {c.get('structured_content')}")
        else:
            text = (c.get("text") or c.get("content") or "")[:150]
            print(f"  [{i}] {chunk_type} | {topic!r} | {text}...")

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Application base URL")
    args = parser.parse_args()

    print("=" * 70)
    print("ANSWER GENERATION TEST (replicates the real user.html flow)")
    print("=" * 70)

    email = prompt("\nEnter your student email address: ")
    if not email:
        print("An email is required for this tool - exiting.")
        sys.exit(1)

    student = lookup_student(args.base_url, email)
    if not student.get("found"):
        print(f"No registered student found with email {email!r} - exiting.")
        print("(This tool only works for already-registered students, matching the real login flow.)")
        sys.exit(1)

    uid = student["uid"]
    class_name = str(student["class"])
    print(f"Welcome, {student.get('name') or email} (Class {class_name}, uid={uid})")

    subject = resolve_subject(args.base_url, class_name)
    import uuid as uuid_lib
    book_uuid = str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, f"{class_name}_{subject}".lower()))

    session_id = None
    while True:
        question = prompt("\nAsk a question (or press Enter to quit): ")
        if not question:
            print("Bye.")
            break

        result = ask_smart_query(args.base_url, uid, question, class_name, subject, book_uuid, session_id)
        session_id = result["session_id"]

        intent = result["intent"]
        print(f"\n{'='*70}")
        print(f"ORCHESTRATOR ROUTING: classification={intent.get('intent')} | "
              f"subject={intent.get('subject')} | chapter={intent.get('chapter')} | "
              f"format={intent.get('format')}")
        print(f"{'='*70}")
        print(f"\nANSWER:\n{result['answer']}")

        retrieval_result = show_retrieval_detail(args.base_url, question, class_name, subject)
        output_path = save_output(email, class_name, subject, session_id, question, result, retrieval_result)
        print(f"[SAVED] {output_path}")
        print()


if __name__ == "__main__":
    main()
