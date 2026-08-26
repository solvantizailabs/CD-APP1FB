"""
Follow-up to backfill_diagram_cleanup.py: that script de-links boilerplate
images from Qdrant/captions.json but never touches the underlying image
FILES, leaving orphaned watermark/margin-icon files behind in both local
disk and the Supabase "book-processing" bucket. This script finds and
deletes those leftover files - purely a storage-hygiene pass, nothing left
for it to affect retrieval or generation (that was already handled by
backfill_diagram_cleanup.py).

Uses the same deterministic (no-LLM) hash-based boilerplate detection,
scanning every file actually present in each chapter's 05_diagrams/images/
folder - not just the ones still referenced by captions.json, since the
whole point here is to find files nothing references anymore.

Usage (dry run by default):
    python -m backend.app.services.new_rag.backfill_diagram_file_cleanup --class 10 --subject social

Apply for real:
    python -m backend.app.services.new_rag.backfill_diagram_file_cleanup --class 10 --subject social --apply
"""
import argparse
import hashlib
import json
import os
import sys
from typing import Dict, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "uploads")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from backend.app.services.new_rag import local_artifacts, supabase_artifacts
from backend.app.services.new_rag.backfill_diagram_cleanup import find_boilerplate_hashes


def collect_proposals(class_name: str, subject: str) -> Dict[str, List[Dict]]:
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")
    to_delete, no_pdf = [], []

    for source_stem in sorted(os.listdir(chapters_root)):
        chapter_dir_path = os.path.join(chapters_root, source_stem)
        status_path = os.path.join(chapter_dir_path, "00_status.json")
        images_dir = os.path.join(chapter_dir_path, "05_diagrams", "images")
        if not os.path.exists(status_path) or not os.path.isdir(images_dir):
            continue
        status = json.load(open(status_path, "r", encoding="utf-8"))
        if status.get("status") != "ingested":
            continue

        pdf_path = os.path.join(UPLOADS_DIR, f"{source_stem}.pdf")
        if not os.path.exists(pdf_path):
            no_pdf.append(source_stem)
            continue

        boilerplate_hashes = find_boilerplate_hashes(pdf_path)
        for fname in sorted(os.listdir(images_dir)):
            fpath = os.path.join(images_dir, fname)
            with open(fpath, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            if file_hash in boilerplate_hashes:
                supabase_rel = f"Class{class_name}_{subject}/chapters/{source_stem}/05_diagrams/images/{fname}"
                to_delete.append({
                    "source_stem": source_stem,
                    "local_path": fpath,
                    "supabase_path": supabase_rel,
                })

    return {"to_delete": to_delete, "no_pdf": no_pdf}


def print_proposals(proposals: Dict[str, List[Dict]]) -> None:
    by_chapter: Dict[str, int] = {}
    for r in proposals["to_delete"]:
        by_chapter[r["source_stem"]] = by_chapter.get(r["source_stem"], 0) + 1
    print("\n=== LEFTOVER BOILERPLATE FILES TO DELETE (local + Supabase) ===")
    if not by_chapter:
        print("  (none)")
    for stem, count in by_chapter.items():
        print(f"  {stem}: {count} files")
    print(f"  TOTAL: {len(proposals['to_delete'])} files")

    print("\n=== CHAPTERS WITH NO SOURCE PDF FOUND (skipped) ===")
    if not proposals["no_pdf"]:
        print("  (none)")
    for s in proposals["no_pdf"]:
        print(f"  {s}")


def apply_deletions(to_delete: List[Dict]) -> None:
    # 1. Local disk first - cheap, synchronous, no network dependency.
    removed_local = 0
    for r in to_delete:
        try:
            os.remove(r["local_path"])
            removed_local += 1
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  [local] failed to remove {r['local_path']}: {e}")
    print(f"[Local] Removed {removed_local}/{len(to_delete)} files")

    # 2. Supabase bulk delete.
    supabase_paths = [r["supabase_path"] for r in to_delete]
    deleted = supabase_artifacts.delete_objects(supabase_paths)
    print(f"[Supabase] Requested deletion of {deleted}/{len(supabase_paths)} objects")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    proposals = collect_proposals(args.class_name, args.subject)
    print_proposals(proposals)

    if not args.apply:
        print("\n(Dry run only - re-run with --apply to delete these files.)")
        return
    if not proposals["to_delete"]:
        print("\nNothing to delete.")
        return

    apply_deletions(proposals["to_delete"])
    print("\nDone.")


if __name__ == "__main__":
    main()
