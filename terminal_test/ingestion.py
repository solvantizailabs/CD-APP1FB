"""
Terminal ingestion test - uploads a book (one or more chapter PDFs) through
the REAL application API, exactly as the UI does, and prints the full
terminal-visible result.

Deliberately has ZERO imports from `backend` - it only ever talks to the
running application over HTTP, hitting the exact same endpoints the UI's
upload flow calls:

    POST /api/upload-multiple   - upload the PDF file(s)
    POST /api/books/pre-analyze - classify + get the chapter confirmation table
    POST /api/books/batch-ingest - start real ingestion (chunking, embedding,
                                    Qdrant, Firestore, Supabase, images - all
                                    of it, whatever the current pipeline does)
    GET  /api/books/status      - poll until the background job finishes

This is the whole point of the design: whatever changes underneath (chunking
strategy, embedding model, Qdrant -> a different vector DB, Supabase -> a
different storage backend, the RAG process itself), this script keeps
working completely unmodified as long as these four endpoint contracts stay
the same - because it never imports or depends on any of that code directly,
only the stable HTTP surface.

Usage:
    python terminal_test/ingestion.py --files "path/to/chapter1.pdf" "path/to/chapter2.pdf" \\
        --class 10 --subject social

    python terminal_test/ingestion.py --files "path/to/single_chapter.pdf" --class 10 --subject maths

Options:
    --base-url   Application base URL (default: http://127.0.0.1:8000)
    --poll-interval  Seconds between status polls (default: 10)
    --poll-timeout   Max seconds to wait for completion (default: 900)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "ingestion")


def save_output(files: list, class_name: str, subject: str, status: dict) -> str:
    """
    Saves this run's request + full ingestion report as JSON under
    outputs/ingestion/ - a versioned record of what was actually ingested
    (per-file status, detected chapter_name, new_rag_chapter_id, or the
    failure reason), so a run can be compared against a later one without
    relying on terminal scrollback.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}_class{class_name}_{subject}.json"
    path = os.path.join(OUTPUTS_DIR, filename)
    payload = {
        "timestamp": timestamp,
        "files": files,
        "class_name": class_name,
        "subject": subject,
        "status": status,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def upload_files(base_url: str, file_paths: list) -> list:
    files = []
    opened = []
    try:
        for path in file_paths:
            if not os.path.exists(path):
                raise FileNotFoundError(f"File not found: {path}")
            f = open(path, "rb")
            opened.append(f)
            files.append(("files", (os.path.basename(path), f, "application/pdf")))
        resp = httpx.post(f"{base_url}/api/upload-multiple", files=files, timeout=120)
    finally:
        for f in opened:
            f.close()
    resp.raise_for_status()
    filenames = resp.json()["filenames"]
    print(f"[UPLOAD] {len(filenames)} file(s) uploaded: {filenames}")
    return filenames


def pre_analyze(base_url: str, filenames: list, class_name: str, subject: str) -> list:
    resp = httpx.post(
        f"{base_url}/api/books/pre-analyze",
        json={"filenames": filenames, "class_name": class_name, "subject": subject},
        timeout=180,
    )
    resp.raise_for_status()
    chapters = resp.json()["chapters"]
    print(f"\n[PRE-ANALYZE] {len(chapters)} chapter(s) classified:")
    for c in chapters:
        print(f"  - {c['filename']}: is_academic={c['is_academic']} "
              f"pages={c['chpstpage']}-{c['chpendpage']} chapter_no={c.get('chapter_no')}")
    return chapters


def start_batch_ingest(base_url: str, class_name: str, subject: str, chapters: list) -> str:
    resp = httpx.post(
        f"{base_url}/api/books/batch-ingest",
        json={"class_name": class_name, "subject": subject, "chapters": chapters},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    book_id = data["book_id"]
    print(f"\n[BATCH-INGEST] Started (book_id={book_id})")
    return book_id


def poll_until_done(base_url: str, class_name: str, subject: str, book_id: str,
                     poll_interval: int, poll_timeout: int) -> dict:
    print(f"\n[STATUS] Polling every {poll_interval}s (timeout {poll_timeout}s)...")
    elapsed = 0
    while elapsed < poll_timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval
        try:
            resp = httpx.get(
                f"{base_url}/api/books/status",
                params={"class_name": class_name, "subject": subject, "book_id": book_id},
                timeout=30,
            )
            resp.raise_for_status()
            status = resp.json()
        except Exception as e:
            # The background job can legitimately keep the server busy enough
            # that a status poll itself times out mid-ingestion (real LLM
            # calls, embedding, uploads all running) - retry rather than
            # treat a single slow poll as a failure.
            print(f"  [{elapsed}s] poll error (server busy, retrying): {e}")
            continue

        print(f"  [{elapsed}s] status={status.get('status')} "
              f"({len(status.get('results', []))}/{status.get('total_chapters', '?')} chapters reported)")

        if status.get("status") in ("completed", "failed"):
            return status

    raise TimeoutError(f"Ingestion did not finish within {poll_timeout}s (book_id={book_id})")


def print_report(status: dict) -> None:
    print(f"\n{'='*70}")
    print(f"INGESTION REPORT - overall status: {status.get('status')}")
    print(f"{'='*70}")
    for r in status.get("results", []):
        marker = {"ingested": "OK", "skipped_non_chapter": "SKIP", "failed": "FAIL"}.get(r.get("status"), "?")
        print(f"[{marker}] {r.get('filename')}")
        print(f"       chapter_name: {r.get('chapter_name')}")
        if r.get("status") == "ingested":
            print(f"       new_rag_chapter_id: {r.get('new_rag_chapter_id')}")
        elif r.get("status") == "failed":
            print(f"       error: {r.get('error')}")
        elif r.get("status") == "skipped_non_chapter":
            print(f"       message: {r.get('message')}")
    if status.get("status") == "failed" and "error" in status:
        print(f"\nFATAL: {status['error']}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--files", nargs="+", required=True, help="One or more PDF file paths (a full book's chapters, or a single chapter)")
    parser.add_argument("--class", dest="class_name", required=True, help="Class/grade, e.g. 10")
    parser.add_argument("--subject", required=True, help="Subject, e.g. social")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Application base URL")
    parser.add_argument("--poll-interval", type=int, default=10, help="Seconds between status polls")
    parser.add_argument("--poll-timeout", type=int, default=900, help="Max seconds to wait for completion")
    args = parser.parse_args()

    print(f"Ingesting {len(args.files)} file(s) for Class {args.class_name} {args.subject}")
    print(f"Target application: {args.base_url}\n")

    filenames = upload_files(args.base_url, args.files)
    chapters = pre_analyze(args.base_url, filenames, args.class_name, args.subject)
    book_id = start_batch_ingest(args.base_url, args.class_name, args.subject, chapters)
    status = poll_until_done(args.base_url, args.class_name, args.subject, book_id,
                              args.poll_interval, args.poll_timeout)
    print_report(status)
    output_path = save_output(args.files, args.class_name, args.subject, status)
    print(f"[SAVED] {output_path}")

    sys.exit(0 if status.get("status") == "completed" else 1)


if __name__ == "__main__":
    main()
