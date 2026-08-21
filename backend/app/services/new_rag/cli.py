"""
Standalone terminal test harness for the new RAG pipeline.

Run from the project root:
    python -m backend.app.services.new_rag.cli

Two options only, per design:
  1) Upload a book (or a chapter's page range within a PDF) - runs the full
     new ingestion pipeline and writes everything it produced to
     backend/app/services/new_rag/outputs/{book_uuid}/ for inspection
     (raw pages, topic manifest, reconstructed chapter markdown, extracted
     diagram images, chunks).
  2) Ask a question - runs the new retrieval logic against whatever has been
     ingested, then optionally generates an answer from the retrieved
     context using the same answer-generation prompt the real app uses
     (answer_service.generate_answer), called standalone. No orchestrator
     call, no personalization, no TTS/video call - just retrieval plus that
     one prompt, so the retrieved chunks and the answer they produce can
     both be inspected and compared against the previous flow.

This does not import, call, or modify chat.py, the orchestrator, the
personalization engine, or the existing textbooks_v2 collection - it is a
fully separate, additive path (see docs/RAG_REDESIGN_PLAN.md, "Testing &
Logging").
"""
import json
import os
import sys
import time
import logging
from typing import Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(message)s")

from dotenv import load_dotenv
load_dotenv()

from backend.app.services.new_rag.pipeline.rag_pipeline import ingest_book, _book_uuid_for
from backend.app.services.new_rag.retrieval.hybrid_retriever import retrieve
from backend.app.services.new_rag import local_artifacts
from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME


def print_banner():
    print("\n" + "=" * 70)
    print("        NEW RAG - STANDALONE TERMINAL TEST HARNESS")
    print("  RAG-only: no orchestrator, no personalization, retrieval only")
    print("=" * 70)


def _ingest_one(pdf_path: str, class_name: str, subject: str,
                 start_page: Optional[int], end_page: Optional[int],
                 chapter_name_override: Optional[str] = None) -> None:
    print(f"\nIngesting {os.path.basename(pdf_path)!r}... (real LLM + embedding calls, "
          "takes a little while - the chapter name is detected automatically from the "
          "chapter's own content, not typed manually)\n")
    t0 = time.time()
    report = ingest_book(
        pdf_path=pdf_path, class_name=class_name, subject=subject,
        chapter_name=chapter_name_override,
        start_pdf_page=start_page or 1, end_pdf_page=end_page,
    )
    elapsed = time.time() - t0

    # pipeline.py itself writes chapter_info.json (the full report) and
    # updates book_index.json at every exit point now - the CLI doesn't
    # need to duplicate that write, just report what happened.
    print("=" * 70)
    print(f"STATUS: {report['status']}")
    print(f"MESSAGE: {report['message']}")
    print(f"DETECTED CHAPTER NAME: {report.get('chapter_name')}")
    print(f"Took {elapsed:.1f}s")
    print(f"Output folder: {report.get('output_dir')}")
    print("=" * 70)

    stage_key = None
    if "stage1" in report["status"]:
        stage_key = "stage1_page_extraction"
    elif "stage2" in report["status"]:
        stage_key = "stage2_topic_detection"

    if stage_key and stage_key in report.get("stages", {}):
        print(f"\nBlocked at {stage_key}. Issues found:")
        for issue in report["stages"][stage_key].get("issues", []):
            print(f"  - {issue}")
        print("This chapter needs manual review before it can be ingested - "
              "see docs/RAG_REDESIGN_PLAN.md sections 3-4 for what these gates check. "
              "(A front-matter/answers-key file correctly showing up here is expected, "
              "not a bug - it has no real topic structure to detect.)")


def upload_book_flow():
    print("\n--- Upload Book / Chapter ---")
    print("Give either a single PDF path (one book/chapter in one file), or a FOLDER path "
          "containing multiple chapter PDFs (the NCERT case - one PDF per chapter, "
          "possibly plus a front-matter PDF). Every .pdf in a folder is processed "
          "automatically. Chapter names are NOT typed manually - the LLM reads each "
          "chapter's own title directly from its content during topic detection.")
    path = input("PDF path or folder path: ").strip()
    if not os.path.exists(path):
        print(f"Not found: {path}")
        return

    class_name = input("Class (e.g. 7): ").strip()
    subject = input("Subject (e.g. science): ").strip()

    if os.path.isdir(path):
        pdf_files = sorted(f for f in os.listdir(path) if f.lower().endswith(".pdf"))
        if not pdf_files:
            print(f"No .pdf files found in {path}")
            return
        # Skip chapters already successfully ingested per book_index.json -
        # without this, re-running the folder after a mid-book failure (the
        # whole reason this loop needs to be re-runnable at all) would
        # re-embed and re-upsert every already-done chapter too. Since each
        # run assigns fresh chunk_id/chapter_id uuids, Qdrant would insert
        # those as brand new points rather than overwriting the existing
        # ones - silent duplicate chunks for every chapter you didn't need
        # to touch again. A chapter that ended in any non-"ingested" status
        # (blocked_stage1/2, failed_stageN_*, skipped_non_chapter) still
        # reruns, since those are exactly the ones that need another attempt.
        existing_index = local_artifacts.load_json(
            local_artifacts.book_dir(class_name, subject), "book_index.json"
        ) or {}
        already_ingested = {
            stem for stem, entry in existing_index.items() if entry.get("status") == "ingested"
        }
        to_process = [f for f in pdf_files
                      if os.path.splitext(f)[0] not in already_ingested]
        skipped = [f for f in pdf_files if f not in to_process]
        if skipped:
            print(f"\nAlready ingested, skipping: {skipped}")
        if not to_process:
            print("Nothing left to ingest - every PDF in this folder is already in book_index.json as 'ingested'.")
            return

        confirm = input(f"Ingest these {len(to_process)} chapter(s)? [y/n]: ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
        failed = []
        for fname in to_process:
            # No fixed inter-chapter sleep needed anymore - every LLM call
            # inside ingest_book now reserves its estimated token cost from
            # the shared rate_governor before firing, which waits exactly as
            # long as the real rolling-window budget requires (zero if free,
            # the real remaining time if not) instead of a blind guess.
            full_path = os.path.join(path, fname)
            try:
                _ingest_one(full_path, class_name, subject, None, None)
            except Exception as e:
                # pipeline.py now catches every LLM call site internally and
                # reports a blocked/failed status instead of raising, but
                # this stays as a backstop for anything else unexpected
                # (e.g. a corrupt PDF the reader itself can't open) - one
                # chapter's failure must never silently stop every remaining
                # chapter in the folder from being attempted, which is what
                # happened before (confirmed live: outputs/Class10_science
                # has only 2 of many chapters in book_index.json because
                # chapter 3 crashed mid-Stage5 with nothing catching it).
                print(f"\n!! Failed to ingest {fname!r}: {e}")
                print("Continuing with the remaining chapters...\n")
                failed.append(fname)
        if failed:
            print(f"\n{len(failed)} chapter(s) raised an unexpected error and need re-running: {failed}")
        print(f"\nDone - {len(to_process)} chapter(s) attempted this run.")

        final_index = local_artifacts.load_json(
            local_artifacts.book_dir(class_name, subject), "book_index.json"
        ) or {}
        print("\n--- Book status (book_index.json) ---")
        for stem in sorted(os.path.splitext(f)[0] for f in pdf_files):
            entry = final_index.get(stem)
            status = entry["status"] if entry else "not yet attempted"
            print(f"  {stem}: {status}")
        print("Front-matter-only PDFs (no real chapter content) showing 'skipped_non_chapter' "
              "are expected, not an error. Anything else that isn't 'ingested' can be re-run by "
              "just running this same folder upload again - already-ingested chapters are skipped "
              "automatically.")
    else:
        start_page_raw = input("Start PDF page (blank = 1): ").strip()
        end_page_raw = input("End PDF page (blank = last page): ").strip()
        start_page = int(start_page_raw) if start_page_raw else None
        end_page = int(end_page_raw) if end_page_raw else None
        _ingest_one(path, class_name, subject, start_page, end_page)


def list_available_books() -> list:
    """
    Queries Qdrant directly for every distinct (class_name, subject, book_uuid,
    chapter_name) combination actually present in textbooks_v3 - added so the
    CLI can show a real "pick your class" menu instead of asking you to type
    class/subject blind, mirroring the actual app's class-selection UX rather
    than just being a raw dev tool.
    """
    client = get_qdrant_client()
    if not client.collection_exists(collection_name=COLLECTION_NAME):
        return []
    hits, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=5000,
        with_payload=["class_name", "subject", "book_uuid", "chapter_name"],
    )
    books = {}
    for h in hits:
        p = h.payload
        key = (p.get("class_name"), p.get("subject"), p.get("book_uuid"))
        if key not in books:
            books[key] = {"class_name": p.get("class_name"), "subject": p.get("subject"),
                          "book_uuid": p.get("book_uuid"), "chapters": set()}
        books[key]["chapters"].add(p.get("chapter_name"))
    return sorted(books.values(), key=lambda b: (str(b["class_name"]), str(b["subject"])))


def ask_question_flow():
    print("\n--- Ask a Question (retrieval only, no answer generation) ---")

    books = list_available_books()
    if not books:
        print("Nothing has been ingested yet - upload a book first (option 1).")
        return

    # Real login by email, same pattern as the existing orchestrator test
    # harness (backend/app/orchestrator_test/test_runner.py::
    # authenticate_student_by_email) - reused rather than reimplemented,
    # since it's a pure read-only Firestore lookup (no password, no writes,
    # no orchestrator/personalization logic touched). This makes the class
    # come from the student's REAL stored profile, same as the real app,
    # instead of being typed freely - falls back to manual class entry if
    # you'd rather not authenticate (e.g. testing a class with no real
    # student account yet).
    email = input("Student email (blank to pick a class manually instead): ").strip()
    class_name = None
    if email:
        from backend.app.orchestrator_test.test_runner import authenticate_student_by_email
        profile = authenticate_student_by_email(email)
        class_name = str(profile["class"])
        print(f"Logged in as {profile['name']} - Class {class_name}, {profile['board']}")

    if class_name:
        matching = [b for b in books if str(b["class_name"]) == class_name]
        if not matching:
            print(f"\nNo ingested content for Class {class_name} yet. Available classes instead:")
            matching = books
    else:
        matching = books

    if len(matching) == 1:
        selected = matching[0]
    else:
        print("\nAvailable subjects:" if class_name else "\nAvailable classes/books:")
        for i, b in enumerate(matching, 1):
            print(f"  {i}) Class {b['class_name']} - {b['subject']}  ({len(b['chapters'])} chapters)")
        choice = input("Pick a number: ").strip()
        try:
            selected = matching[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            return

    class_name, subject, book_uuid = selected["class_name"], selected["subject"], selected["book_uuid"]
    print(f"\nSelected: Class {class_name} - {subject}")
    print("Chapters available:", ", ".join(sorted(c for c in selected["chapters"] if c)))

    query = input("\nYour question: ").strip()
    if not query:
        return

    print("\nRetrieving...\n")
    result = retrieve(query, book_uuid, class_name=class_name, subject=subject)

    print("=" * 70)
    print(f"STATUS: {result['status']}   confidence_tier={result.get('confidence_tier')}   "
          f"top_score={result.get('top_score')}   top_k={result.get('top_k')}")
    print(f"retried={result.get('retried')}   escalated_to_parent={result.get('escalated_to_parent')}")
    print("=" * 70)

    chunks = result.get("chunks", result.get("best_attempt_chunks", []))
    if not chunks:
        print("No chunks retrieved. Did you ingest this class/subject yet?")
    print("\nFINAL ANSWER CHUNK(S) (what would actually reach the LLM):")
    for i, c in enumerate(chunks, 1):
        p = c["payload"]
        print(f"\n[{i}] chunk_type={p.get('chunk_type')}  topic={p.get('topic_name')!r}  "
              f"pages={p.get('start_page')}-{p.get('end_page')}  score={c.get('rerank_score')}")
        print(f"    {p.get('text', '')[:300]}")

    # child_candidates are the actual child-level search/rerank results,
    # preserved even when escalated_to_parent=True swapped the final answer
    # to a parent - without printing/saving these too, "what were the
    # underlying children, why did this escalate" was unanswerable after
    # the fact once the terminal scrolled past.
    child_candidates = result.get("child_candidates", [])
    if result.get("escalated_to_parent") and child_candidates:
        escalated_n = result.get("escalated_parent_count", 1)
        print(f"\nUNDERLYING CHILD CHUNKS CONSIDERED ({len(child_candidates)}, before parent escalation "
              f"to {escalated_n} parent(s)):")
        for i, c in enumerate(child_candidates, 1):
            p = c["payload"]
            print(f"  [{i}] chunk_type={p.get('chunk_type')}  topic={p.get('topic_name')!r}  "
                  f"parent_chunk_id={p.get('parent_chunk_id')}  pages={p.get('start_page')}-{p.get('end_page')}  "
                  f"score={c.get('rerank_score')}")

    # full_candidate_pool is everything that competed (up to ~20, deduped +
    # reranked), not just the top-k that made the final cut - kept visible
    # per explicit request, so "what were ALL the child chunks retrieved,
    # not only the top ranker" is answerable from this one file.
    full_pool = result.get("full_candidate_pool", [])
    print(f"\nFULL CANDIDATE POOL ({len(full_pool)} total, deduped + reranked, before the top_k={result.get('top_k')} cut):")
    for i, c in enumerate(full_pool, 1):
        p = c["payload"]
        print(f"  [{i}] chunk_type={p.get('chunk_type')}  topic={p.get('topic_name')!r}  "
              f"pages={p.get('start_page')}-{p.get('end_page')}  score={c.get('rerank_score')}")

    # Phase 6/7 - compression + the CTO spec's exact context package shape +
    # grounding readiness. Reuses the chunks already retrieved above rather
    # than calling retrieve() a second time via retrieve_as_package().
    package = None
    generated_answer = None
    if chunks:
        from backend.app.services.new_rag.context.compressor import compress
        from backend.app.services.new_rag.context.context_builder import build_context_package
        from backend.app.services.new_rag.validation.retrieval_validator import validate_retrieval_result

        compressed_chunks, total_tokens = compress(query, chunks)
        package = build_context_package(query, result, class_name=class_name, subject=subject)
        is_result_valid, result_issues = validate_retrieval_result(result)

        print("\n" + "-" * 70)
        print(f"CONTEXT PACKAGE (Phase 6): compressed to ~{total_tokens} tokens across "
              f"{len(compressed_chunks)} chunk(s)")
        print(f"confidence={package['confidence']}  retrieval_status={package['retrieval_status']}")
        print(f"result_validation: {'OK' if is_result_valid else 'ISSUES: ' + '; '.join(result_issues)}")
        print(f"context preview:\n{package['context'][:400]}{'...' if len(package['context']) > 400 else ''}")
        print("-" * 70)

        # Answer generation - same prompt/model the real app uses
        # (answer_service.generate_answer), called standalone: no
        # orchestrator, no personalization, no TTS/video call. Runs
        # automatically for every question (not gated behind a prompt) since
        # this is exactly what the report exists to capture: how the new
        # RAG's retrieved chunks change both the quick-answer text mode and
        # the video transcript mode, for comparison against the previous
        # flow's answers to the same questions. GENERATE_ANSWER_SYSTEM
        # always produces both TEXT_RESPONSE (quick-answer mode) and
        # VOICE_SCRIPT (video-transcript mode) in the one call - no separate
        # calls or mode selection needed.
        print("\nGenerating answer (quick-answer text + video transcript)...\n")
        from backend.app.services.new_rag.answer_test import generate_test_answer
        generated_answer = generate_test_answer(query, package["context"], class_name, subject)
        print("=" * 70)
        print("GENERATED ANSWER - QUICK-ANSWER MODE (TEXT_RESPONSE):")
        print(generated_answer["text_response"] or "(none - markers not found in response)")
        print("\nGENERATED ANSWER - VIDEO MODE (VOICE_SCRIPT / transcript):")
        print(generated_answer["voice_script"] or "(none - markers not found in response)")
        print("=" * 70)

    # write a per-query report, same convention as the existing
    # orchestrator_test/test_outputs/query_report_*.json pattern
    out_dir = local_artifacts.queries_dir(class_name, subject)
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(out_dir, f"query_report_{ts}.json")
    serializable_chunks = [{"payload": c["payload"], "rerank_score": c.get("rerank_score")} for c in chunks]
    serializable_child_candidates = [
        {"payload": c["payload"], "rerank_score": c.get("rerank_score")} for c in child_candidates
    ]
    serializable_full_pool = [
        {"payload": c["payload"], "rerank_score": c.get("rerank_score")} for c in full_pool
    ]
    if package is not None:
        with open(report_path.replace("query_report_", "context_package_"), "w", encoding="utf-8") as f:
            json.dump(package, f, indent=2, ensure_ascii=False)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "query": query, "status": result["status"], "top_score": result.get("top_score"),
            "retried": result.get("retried"), "escalated_to_parent": result.get("escalated_to_parent"),
            "escalated_parent_count": result.get("escalated_parent_count", 0),
            "chunks": serializable_chunks,
            "child_candidates": serializable_child_candidates,
            "full_candidate_pool": serializable_full_pool,
            "generated_answer": generated_answer,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {report_path}")


def main():
    print_banner()
    while True:
        print("\n1) Upload book / chapter")
        print("2) Ask a question")
        print("3) Quit")
        choice = input("> ").strip()
        if choice == "1":
            upload_book_flow()
        elif choice == "2":
            ask_question_flow()
        elif choice == "3":
            break
        else:
            print("Enter 1, 2, or 3.")


if __name__ == "__main__":
    main()
