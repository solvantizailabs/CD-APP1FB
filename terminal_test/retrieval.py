"""
Terminal retrieval test - ask a question against an ingested book and see
every chunk the retriever actually returns, through the REAL application
endpoints, exactly like ingestion.py does for uploads.

Deliberately has ZERO imports from `backend` - only ever talks to the
running application over HTTP:

    GET /api/students/lookup - resolve a student's class from their email
    GET /api/subjects        - list books available for a class
    GET /api/books/all       - list every book across every class (no-login path)
    GET /api/retrieve        - run retrieval ONLY (no orchestrator, no
                                generation, no TTS) and return every chunk

Same design principle as ingestion.py: whatever changes underneath
(chunking, embedding model, the vector DB itself, retrieval logic), this
script keeps working unmodified as long as these endpoint contracts hold.

Usage:
    python terminal_test/retrieval.py
    python terminal_test/retrieval.py --base-url http://127.0.0.1:8000
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "retrieval")


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "query"


def normalized_score(raw_score) -> float:
    """
    Maps the reranker's raw, unbounded score (a cross-encoder logit - can be
    any real number, negative included, NOT a 0-1 similarity) onto a 0-100
    scale via a sigmoid, purely for human readability. This does not change
    ranking at all (sigmoid is monotonic - same order, same relative gaps in
    spirit) - it exists because "score=-9.7" reads as an error to someone
    expecting a bounded similarity score, when it's actually just how this
    class of model expresses "poor match". A negative raw score has never
    been a bug on its own; this is a display convenience, not a fix.
    """
    if raw_score is None:
        return None
    try:
        return round(100 / (1 + math.exp(-float(raw_score))), 1)
    except (TypeError, ValueError, OverflowError):
        return None


def _summarize_chunk(c: dict) -> dict:
    """Compact, consistent shape for a chunk in the saved JSON/printed
    output - used for both child candidates and final chunks so the two
    sections are directly comparable side by side."""
    return {
        "level": c.get("level", "child"),
        "score": c.get("score"),
        "score_0_100": normalized_score(c.get("score")),
        "chunk_type": c.get("chunk_type"),
        "topic_name": c.get("topic_name"),
        "parent_chunk_id": c.get("parent_chunk_id"),
        "note": c.get("note"),
        "text_or_caption": ((c.get("text") or c.get("content") or "")[:200]),
        "image_url": c.get("structured_content") if c.get("chunk_type") == "diagram" else None,
    }


def save_output(query: str, class_name: str, subject: str, result: dict) -> str:
    """
    Saves this run's request + a structured breakdown of the retrieval
    result as JSON under outputs/retrieval/ - a versioned record of not
    just the final answer chunks, but the full child-chunk candidate pool
    that was actually searched/reranked, the per-parent-topic vote count
    behind any escalation decision, and which final chunks are parent-level
    vs. child-level - added 2026-08-25 per user request, so "why did this
    escalate to THIS parent and not another" and "was this a full parent or
    just one child" are answerable from the saved file alone, without
    re-running the query or reading source code.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}_{_slug(query)}.json"
    path = os.path.join(OUTPUTS_DIR, filename)

    payload = {
        "timestamp": timestamp,
        "query": query,
        "class_name": class_name,
        "subject": subject,
        "status": result.get("status"),
        "confidence_tier": result.get("confidence_tier"),
        "top_score": result.get("top_score"),
        "top_score_0_100": normalized_score(result.get("top_score")),
        "retried": result.get("retried"),

        "1_child_chunks_retrieved": {
            "description": "Every child-level candidate actually searched and reranked, "
                            "before any parent-escalation decision was applied.",
            "count": len(result.get("child_candidates", [])),
            "chunks": [_summarize_chunk(c) for c in result.get("child_candidates", [])],
        },

        "2_parent_escalation_decision": {
            "description": "For each distinct parent topic among the child chunks above: "
                            "how many candidates fell under it, and whether that was enough "
                            "to trigger escalation (a topic wins if its share meets the threshold below).",
            "escalated_to_parent": result.get("escalated_to_parent"),
            "escalation_threshold_count": result.get("escalation_threshold_count"),
            "parent_vote_breakdown": result.get("parent_vote_breakdown", []),
        },

        "3_final_chunks_sent_to_llm": {
            "description": "What actually gets used downstream (ground_text_narration's "
                            "TEXTBOOK CONTEXT + real image attachments). 'level': 'parent' means "
                            "the FULL topic text was substituted in place of scattered children; "
                            "'level': 'child' means this is still an individual chunk (either "
                            "escalation didn't trigger, or this chunk was specifically retained/"
                            "re-attached alongside an escalated parent - see its 'note').",
            "count": result.get("chunk_count"),
            "chunks": [_summarize_chunk(c) for c in result.get("chunks", [])],
        },

        "raw_result": result,
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


def list_books_for_class(base_url: str, class_name: str) -> list:
    resp = httpx.get(f"{base_url}/api/subjects", params={"class_name": class_name}, timeout=15)
    resp.raise_for_status()
    subjects = resp.json().get("subjects", [])
    # /api/subjects returns every CONFIGURED subject for the class (icons,
    # display names) regardless of whether it's actually been ingested -
    # filter through /api/books/all so only books with real content ever
    # get offered as a selectable choice.
    all_books = list_all_books(base_url)
    ingested_subjects = {b["subject"].lower() for b in all_books if b["class_name"] == str(class_name)}
    names = [s.get("name") for s in subjects if s.get("name")]
    return [
        {"class_name": str(class_name), "subject": s}
        for s in names if s.lower() in ingested_subjects
    ]


def list_all_books(base_url: str) -> list:
    resp = httpx.get(f"{base_url}/api/books/all", timeout=15)
    resp.raise_for_status()
    return resp.json().get("books", [])


def choose_book(books: list) -> dict:
    if not books:
        print("No ingested books found.")
        sys.exit(1)
    print("\nAvailable books:")
    for i, b in enumerate(books, start=1):
        print(f"  {i}. Class {b['class_name']} - {b['subject']} ({b.get('chapter_count', '?')} chapter(s))")
    while True:
        choice = prompt(f"\nSelect a book [1-{len(books)}]: ")
        if choice.isdigit() and 1 <= int(choice) <= len(books):
            return books[int(choice) - 1]
        print("Invalid choice, try again.")


def retrieve(base_url: str, query: str, class_name: str, subject: str) -> dict:
    resp = httpx.get(
        f"{base_url}/api/retrieve",
        params={"query": query, "class_name": class_name, "subject": subject},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def print_chunks(result: dict) -> None:
    print(f"\n{'='*70}")
    print(f"RETRIEVAL RESULT")
    print(f"{'='*70}")
    print(f"status: {result.get('status')} | confidence: {result.get('confidence_tier')} | "
          f"top_score: {result.get('top_score')} (~{normalized_score(result.get('top_score'))}/100) | "
          f"retried: {result.get('retried')} | escalated_to_parent: {result.get('escalated_to_parent')}")

    print(f"\n{'-'*70}\n1. CHILD CHUNKS RETRIEVED (before any parent escalation)\n{'-'*70}")
    for i, c in enumerate(result.get("child_candidates", []), start=1):
        s = _summarize_chunk(c)
        print(f"[{i}] score={s['score']} (~{s['score_0_100']}/100) | type={s['chunk_type']} | "
              f"topic={s['topic_name']!r} | parent_chunk_id={s['parent_chunk_id']}")

    print(f"\n{'-'*70}\n2. PARENT ESCALATION DECISION\n{'-'*70}")
    print(f"escalated_to_parent={result.get('escalated_to_parent')} | "
          f"threshold={result.get('escalation_threshold_count')} of "
          f"{len(result.get('child_candidates', []))} child chunks")
    for v in result.get("parent_vote_breakdown", []):
        mark = "WINS" if v.get("qualifies_for_escalation") else "    "
        print(f"  [{mark}] topic={v.get('topic_name')!r} | votes={v.get('share')}")

    print(f"\n{'-'*70}\n3. FINAL CHUNKS SENT TO LLM\n{'-'*70}")
    for i, c in enumerate(result.get("chunks", []), start=1):
        s = _summarize_chunk(c)
        print(f"\n[{i}] level={s['level']} | score={s['score']} (~{s['score_0_100']}/100) | "
              f"type={s['chunk_type']} | topic={s['topic_name']!r}")
        if s["note"]:
            print(f"    note: {s['note']}")
        if s["image_url"]:
            print(f"    image_url: {s['image_url']}")
        print(f"    text: {s['text_or_caption']}{'...' if len(s['text_or_caption']) >= 200 else ''}")
    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Application base URL")
    args = parser.parse_args()

    print("=" * 70)
    print("RETRIEVAL TEST")
    print("=" * 70)

    email = prompt("\nEnter your student email address (press Enter to skip): ")

    books = None
    if email:
        student = lookup_student(args.base_url, email)
        if student.get("found"):
            print(f"Welcome, {student.get('name') or email} (Class {student.get('class')})")
            books = list_books_for_class(args.base_url, student["class"])
            if not books:
                print(f"No ingested books found for Class {student['class']} - showing all books instead.")
        else:
            print(f"No student found with email {email!r} - showing all books instead.")

    if books is None:
        books = list_all_books(args.base_url)

    book = choose_book(books)
    print(f"\nSelected: Class {book['class_name']} - {book['subject']}")

    while True:
        question = prompt("\nEnter your question (or press Enter to quit): ")
        if not question:
            print("Bye.")
            break
        result = retrieve(args.base_url, question, book["class_name"], book["subject"])
        print_chunks(result)
        output_path = save_output(question, book["class_name"], book["subject"], result)
        print(f"[SAVED] {output_path}")


if __name__ == "__main__":
    main()
