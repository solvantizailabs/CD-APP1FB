"""
One-off backfill: rewrites `structured_content` from a local-disk relative
path to the real Supabase public URL, for diagram chunks ingested before
Stage 1's URL-storage fix (see docs/IMAGE_PIPELINE_PLAN.md section 2.3).
The image FILE was already uploaded to Supabase at ingestion time
regardless of what path string got stored - this only fixes the stored
string, no re-upload needed.

Matters because Stage 3's image-attachment code (ground_text_narration in
test_runner.py) only ever attaches a diagram whose structured_content
starts with "http" - a local path is silently (and correctly) skipped,
which meant no already-ingested diagram could ever actually reach the LLM
until this backfill runs.

Usage (dry run by default):
    python -m backend.app.services.new_rag.backfill_diagram_urls --class 10 --subject social

Apply for real:
    python -m backend.app.services.new_rag.backfill_diagram_urls --class 10 --subject social --apply
"""
import argparse
import json
import os
import sys
from typing import Dict, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from backend.app.services.new_rag import local_artifacts, supabase_artifacts
from backend.app.core.supabase_storage import get_supabase_config

BUCKET_NAME = "book-processing"


def collect_proposals(class_name: str, subject: str) -> Dict[str, List[Dict]]:
    supabase_url, _ = get_supabase_config()
    book_dir = local_artifacts.book_dir(class_name, subject)
    book_key = os.path.basename(book_dir)
    chapters_root = os.path.join(book_dir, "chapters")
    to_fix = []

    for source_stem in sorted(os.listdir(chapters_root)):
        chdir = os.path.join(chapters_root, source_stem)
        status_path = os.path.join(chdir, "00_status.json")
        captions_path = os.path.join(chdir, "05_diagrams", "captions.json")
        if not os.path.exists(status_path) or not os.path.exists(captions_path):
            continue
        status = json.load(open(status_path, "r", encoding="utf-8"))
        if status.get("status") != "ingested":
            continue

        captions = json.load(open(captions_path, "r", encoding="utf-8"))
        for c in captions:
            sc = c.get("structured_content", "")
            if sc and not sc.startswith("http"):
                real_url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{book_key}/chapters/{source_stem}/{sc}"
                to_fix.append({
                    "source_stem": source_stem,
                    "chunk_id": c["chunk_id"],
                    "old": sc,
                    "new": real_url,
                })

    return {"to_fix": to_fix}


def print_proposals(proposals: Dict[str, List[Dict]]) -> None:
    by_chapter: Dict[str, int] = {}
    for r in proposals["to_fix"]:
        by_chapter[r["source_stem"]] = by_chapter.get(r["source_stem"], 0) + 1
    print("\n=== DIAGRAM URLS TO FIX (local path -> real Supabase URL) ===")
    if not by_chapter:
        print("  (none - all already correct)")
    for stem, count in by_chapter.items():
        print(f"  {stem}: {count} chunks")
    print(f"  TOTAL: {len(proposals['to_fix'])} chunks")


def apply_fixes(class_name: str, subject: str, to_fix: List[Dict]) -> None:
    from qdrant_client import models
    from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME
    from backend.app.services.new_rag.indexing.image_indexer import IMAGE_COLLECTION_NAME

    client = get_qdrant_client()
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")

    # 1. Qdrant textbooks_v3 - one set_payload per chunk (each has a distinct
    #    new URL, so this can't be a single bulk filter update).
    for r in to_fix:
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"structured_content": r["new"]},
            points=[r["chunk_id"]],
        )
    print(f"[Qdrant textbooks_v3] Updated structured_content on {len(to_fix)} chunks")

    # 2. Qdrant textbook_diagrams_v1 - same chunk_id is the point ID there,
    #    same field name is "image_url" not "structured_content".
    if client.collection_exists(collection_name=IMAGE_COLLECTION_NAME):
        for r in to_fix:
            try:
                client.set_payload(
                    collection_name=IMAGE_COLLECTION_NAME,
                    payload={"image_url": r["new"]},
                    points=[r["chunk_id"]],
                )
            except Exception:
                pass  # point may not exist there yet if image-embedding backfill hasn't run for this chapter
        print(f"[Qdrant {IMAGE_COLLECTION_NAME}] Updated image_url where present")

    # 3. Local captions.json + Supabase mirror, per chapter.
    by_chapter: Dict[str, List[Dict]] = {}
    for r in to_fix:
        by_chapter.setdefault(r["source_stem"], []).append(r)

    for source_stem, fixes in by_chapter.items():
        by_chunk_id = {f["chunk_id"]: f["new"] for f in fixes}
        captions_path = os.path.join(chapters_root, source_stem, "05_diagrams", "captions.json")
        captions = json.load(open(captions_path, "r", encoding="utf-8"))
        for c in captions:
            if c["chunk_id"] in by_chunk_id:
                c["structured_content"] = by_chunk_id[c["chunk_id"]]
        with open(captions_path, "w", encoding="utf-8") as f:
            json.dump(captions, f, indent=2, ensure_ascii=False)
        supabase_artifacts.upload_binary(
            captions_path, os.path.relpath(captions_path, local_artifacts.OUTPUT_ROOT).replace(os.sep, "/")
        )
        print(f"[Local/Supabase] {source_stem}: captions.json updated ({len(fixes)} entries)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    proposals = collect_proposals(args.class_name, args.subject)
    print_proposals(proposals)

    if not args.apply:
        print("\n(Dry run only - re-run with --apply to write these.)")
        return
    if not proposals["to_fix"]:
        print("\nNothing to fix.")
        return

    apply_fixes(args.class_name, args.subject, proposals["to_fix"])
    print("\nDone.")


if __name__ == "__main__":
    main()
