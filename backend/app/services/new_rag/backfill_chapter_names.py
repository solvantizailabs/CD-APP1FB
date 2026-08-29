"""
One-off backfill for chapters ingested before the chapter-naming fix
(pre-analyze's shallow, 2-page LLM guess was overriding new_rag's own
full-chapter-text detection - see books.py::pre_analyze_books /
rag_pipeline.py::ingest_book). Those chapters already have the CORRECT name
sitting unused in `detected_chapter_title` inside 00_status.json - this
script promotes it to `chapter_name` everywhere that field is stored
(Qdrant textbooks_v3 payloads, the Firestore chapter-summary doc, and the
local/Supabase-mirrored artifact files), with NO re-ingestion, no LLM calls,
and no re-embedding.

Usage (dry run by default - only prints the proposed changes):
    python -m backend.app.services.new_rag.backfill_chapter_names --class 10 --subject social

Apply for real once the printed table looks right:
    python -m backend.app.services.new_rag.backfill_chapter_names --class 10 --subject social --apply

A chapter whose detected_chapter_title looks corrupted (isolated single
capital letters from a garbled running-header extraction, e.g. "I NDIA",
"R ESOURCES") is never auto-applied - it's listed separately under
"NEEDS MANUAL REVIEW" and left untouched either way.
"""
import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from backend.app.services.new_rag import local_artifacts, supabase_artifacts

_GARBLED_RE = re.compile(r"\b[A-Za-z]\s[A-Za-z]{2,}\b")


def _clean(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _looks_garbled(title: str) -> bool:
    return "�" in title or bool(_GARBLED_RE.search(title))


def collect_proposals(class_name: str, subject: str) -> Dict[str, List[Dict]]:
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")
    changes, needs_review, no_change = [], [], []

    for source_stem in sorted(os.listdir(chapters_root)):
        status_path = os.path.join(chapters_root, source_stem, "00_status.json")
        if not os.path.exists(status_path):
            continue
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)

        if status.get("status") != "ingested":
            continue

        current = status.get("chapter_name")
        detected = status.get("detected_chapter_title")
        row = {
            "source_stem": source_stem,
            "chapter_id": status.get("chapter_id"),
            "current_name": current,
            "detected_title": detected,
        }

        if not detected:
            needs_review.append({**row, "reason": "no detected_chapter_title recorded"})
        elif _looks_garbled(detected):
            needs_review.append({**row, "reason": "detected_chapter_title looks corrupted (garbled extraction)"})
        elif _clean(detected) == _clean(current or ""):
            no_change.append(row)
        else:
            row["new_name"] = _clean(detected)
            changes.append(row)

    return {"changes": changes, "needs_review": needs_review, "no_change": no_change}


def print_proposals(proposals: Dict[str, List[Dict]]) -> None:
    print("\n=== PROPOSED CHANGES ===")
    if not proposals["changes"]:
        print("  (none)")
    for r in proposals["changes"]:
        print(f"  {r['source_stem']}: {r['current_name']!r} -> {r['new_name']!r}")

    print("\n=== NEEDS MANUAL REVIEW (left untouched) ===")
    if not proposals["needs_review"]:
        print("  (none)")
    for r in proposals["needs_review"]:
        print(f"  {r['source_stem']}: current={r['current_name']!r} detected={r['detected_title']!r} - {r['reason']}")

    print("\n=== ALREADY CORRECT (no change needed) ===")
    if not proposals["no_change"]:
        print("  (none)")
    for r in proposals["no_change"]:
        print(f"  {r['source_stem']}: {r['current_name']!r}")


def apply_changes(class_name: str, subject: str, changes: List[Dict]) -> None:
    from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME
    from qdrant_client import models
    from backend.app.core import firestore_service

    client = get_qdrant_client()
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")

    # 1. Qdrant payloads (textbooks_v3) - rewrite chapter_name for every chunk
    #    tagged with this chapter_id, no re-embedding needed.
    for r in changes:
        result = client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"chapter_name": r["new_name"]},
            points=models.Filter(
                must=[models.FieldCondition(key="chapter_id", match=models.MatchValue(value=r["chapter_id"]))]
            ),
        )
        print(f"[Qdrant] {r['source_stem']}: chapter_name -> {r['new_name']!r} ({result})")

    # 2. Local artifact files (00_status.json, book_index.json) + their
    #    Supabase mirror, so future reads of these files see the fix too.
    for r in changes:
        dir_path = os.path.join(chapters_root, r["source_stem"])
        status_path = os.path.join(dir_path, "00_status.json")
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
        status["chapter_name"] = r["new_name"]
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
        supabase_artifacts.upload_binary(status_path, os.path.relpath(status_path, local_artifacts.OUTPUT_ROOT).replace(os.sep, "/"))

        index_path = local_artifacts.update_book_index(class_name, subject, r["source_stem"], {
            "chapter_name": r["new_name"], "status": status.get("status"),
            "message": status.get("message"), "folder": r["source_stem"],
        })
        supabase_artifacts.upload_binary(index_path, os.path.relpath(index_path, local_artifacts.OUTPUT_ROOT).replace(os.sep, "/"))
        print(f"[Local/Supabase] {r['source_stem']}: 00_status.json + book_index.json updated")

    # 3. Firestore classes/{class}/subjects/{subject}.chapters[] - match by
    #    new_rag_chapter_id (the field books.py stamped from ingest_report
    #    when this chapter was first ingested), not by the old wrong name.
    doc = firestore_service.load_summary_from_firestore(class_name, subject)
    if doc and doc.get("chapters"):
        by_chapter_id = {r["chapter_id"]: r["new_name"] for r in changes}
        updated = False
        for ch in doc["chapters"]:
            new_name = by_chapter_id.get(ch.get("new_rag_chapter_id"))
            if new_name and ch.get("chapter_name") != new_name:
                print(f"[Firestore] {ch.get('chapter_name')!r} -> {new_name!r}")
                ch["chapter_name"] = new_name
                updated = True
        if updated:
            firestore_service.save_summary_document(class_name, subject, doc.get("book_uuid"), doc["chapters"])
        else:
            print("[Firestore] No matching chapters found by new_rag_chapter_id - nothing updated "
                  "(this book may predate that field being stamped; Qdrant/local fixes above still applied).")
    else:
        print("[Firestore] No summary document found for this class/subject - skipped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually write the changes (default: dry run only)")
    args = parser.parse_args()

    proposals = collect_proposals(args.class_name, args.subject)
    print_proposals(proposals)

    if not args.apply:
        print("\n(Dry run only - re-run with --apply to write these changes to Qdrant/Firestore/local+Supabase.)")
        return

    if not proposals["changes"]:
        print("\nNo changes to apply.")
        return

    apply_changes(args.class_name, args.subject, proposals["changes"])
    print("\nDone.")


if __name__ == "__main__":
    main()
