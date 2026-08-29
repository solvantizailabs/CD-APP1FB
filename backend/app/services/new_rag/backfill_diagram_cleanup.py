"""
One-off cleanup for chapters ingested before the boilerplate-image fix (see
ingestion/pdf_parser.py::flag_boilerplate_images). Those chapters have
"diagram" chunks in Qdrant whose caption is a hallucinated description of a
recurring, non-content image (the NCERT "not to be republished" watermark,
repeated margin icons) - confirmed live (jess102) that the same
byte-identical image gets a different, fabricated, chapter-topic-specific
caption on every page it appears on, since the captioning LLM was given
per-page topic context with nothing in the pixels to actually describe.

This script does NOT re-run captioning or re-ingest anything - it only
DELETES the chunks that are already known to be boilerplate, using the same
deterministic (no-LLM) hash-based detection now used at ingestion time.
Requires the chapter's original source PDF (used only to recompute which
extracted images are boilerplate - no LLM calls, no re-embedding of the
chunks that survive).

Usage (dry run by default - only prints what would be deleted):
    python -m backend.app.services.new_rag.backfill_diagram_cleanup --class 10 --subject social

Apply for real once the printed list looks right:
    python -m backend.app.services.new_rag.backfill_diagram_cleanup --class 10 --subject social --apply
"""
import argparse
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional, Set

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "uploads")

# Windows' console codepage (cp1252) can't encode every character an LLM
# caption might contain (confirmed live: a "Δ" Greek Delta in a maths
# caption crashed a plain print() mid-run, silently truncating the dry-run
# listing before it ever reached later chapters or the summary sections -
# dangerous for a script whose whole point is to show a complete list before
# an --apply deletes anything). Reconfigure stdout to UTF-8 unconditionally
# so a caption's characters never crash the report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from backend.app.services.new_rag import local_artifacts, supabase_artifacts
from backend.app.services.new_rag.ingestion.pdf_parser import extract_diagram_images, flag_boilerplate_images

def find_boilerplate_hashes(pdf_path: str) -> Set[str]:
    """Returns the set of sha256 image hashes that a fresh, deterministic
    extraction identifies as boilerplate (recurring watermark/margin icon)
    for this PDF - see flag_boilerplate_images()."""
    from pypdf import PdfReader
    total_pages = len(PdfReader(pdf_path).pages)
    diagrams = extract_diagram_images(pdf_path, 1, total_pages)
    diagrams = flag_boilerplate_images(diagrams, total_pages=total_pages)
    return {d["image_hash"] for d in diagrams if d["is_boilerplate"]}


def _local_image_hash(chapter_dir_path: str, structured_content: str) -> Optional[str]:
    """
    Hashes the actual already-saved local image file a stored chunk points
    at, rather than trying to re-derive a join key from the extraction
    order (page/loop-index) - deliberately avoids any assumption that
    today's extraction would enumerate a page's images in the exact same
    order the original ingestion did. structured_content may be a bare
    relative local path (older chunks) or, after the URL fix, a Supabase
    URL - only the local-path form can be hashed directly; a URL form
    means no local copy is expected to exist under this chapter's folder,
    so it's skipped rather than guessed at.
    """
    if not structured_content or structured_content.startswith("http"):
        return None
    local_path = os.path.join(chapter_dir_path, *structured_content.split("/"))
    if not os.path.exists(local_path):
        return None
    with open(local_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def collect_proposals(class_name: str, subject: str) -> Dict[str, List[Dict]]:
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")
    to_delete, no_pdf, no_boilerplate = [], [], []

    for source_stem in sorted(os.listdir(chapters_root)):
        chapter_dir_path = os.path.join(chapters_root, source_stem)
        status_path = os.path.join(chapter_dir_path, "00_status.json")
        captions_path = os.path.join(chapter_dir_path, "05_diagrams", "captions.json")
        if not os.path.exists(status_path) or not os.path.exists(captions_path):
            continue
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
        if status.get("status") != "ingested":
            continue

        pdf_path = os.path.join(UPLOADS_DIR, f"{source_stem}.pdf")
        if not os.path.exists(pdf_path):
            no_pdf.append(source_stem)
            continue

        boilerplate_hashes = find_boilerplate_hashes(pdf_path)
        if not boilerplate_hashes:
            no_boilerplate.append(source_stem)
            continue

        with open(captions_path, "r", encoding="utf-8") as f:
            diagram_chunks = json.load(f)

        for chunk in diagram_chunks:
            file_hash = _local_image_hash(chapter_dir_path, chunk.get("structured_content", ""))
            if file_hash and file_hash in boilerplate_hashes:
                to_delete.append({
                    "source_stem": source_stem,
                    "chapter_id": status["chapter_id"],
                    "chunk_id": chunk["chunk_id"],
                    "caption_preview": chunk.get("text", "")[:80],
                    "structured_content": chunk.get("structured_content"),
                })

    return {"to_delete": to_delete, "no_pdf": no_pdf, "no_boilerplate": no_boilerplate}


def print_proposals(proposals: Dict[str, List[Dict]]) -> None:
    print("\n=== DIAGRAM CHUNKS TO DELETE (hallucinated boilerplate captions) ===")
    if not proposals["to_delete"]:
        print("  (none)")
    for r in proposals["to_delete"]:
        print(f"  {r['source_stem']} | {r['structured_content']} | {r['caption_preview']!r}")

    print(f"\n=== CHAPTERS WITH NO SOURCE PDF FOUND (skipped, uploads/{{stem}}.pdf missing) ===")
    if not proposals["no_pdf"]:
        print("  (none)")
    for s in proposals["no_pdf"]:
        print(f"  {s}")

    print("\n=== CHAPTERS WITH NO BOILERPLATE DETECTED (nothing to clean) ===")
    if not proposals["no_boilerplate"]:
        print("  (none)")
    for s in proposals["no_boilerplate"]:
        print(f"  {s}")


def apply_deletions(class_name: str, subject: str, to_delete: List[Dict]) -> None:
    from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME
    from qdrant_client import models

    client = get_qdrant_client()
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")

    # 1. Delete the points from Qdrant by their real point IDs - no re-embedding,
    #    no re-upsert of the chunks that survive.
    chunk_ids = [r["chunk_id"] for r in to_delete]
    result = client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.PointIdsList(points=chunk_ids),
    )
    print(f"[Qdrant] Deleted {len(chunk_ids)} points ({result})")

    # 2. Rewrite each affected chapter's local captions.json (and its
    #    Supabase mirror) to drop the same chunks, so future reads of this
    #    file don't resurrect the boilerplate entries.
    by_chapter = {}
    for r in to_delete:
        by_chapter.setdefault(r["source_stem"], set()).add(r["chunk_id"])

    for source_stem, dead_ids in by_chapter.items():
        captions_path = os.path.join(chapters_root, source_stem, "05_diagrams", "captions.json")
        with open(captions_path, "r", encoding="utf-8") as f:
            diagram_chunks = json.load(f)
        kept = [c for c in diagram_chunks if c["chunk_id"] not in dead_ids]
        removed = len(diagram_chunks) - len(kept)
        with open(captions_path, "w", encoding="utf-8") as f:
            json.dump(kept, f, indent=2, ensure_ascii=False)
        supabase_artifacts.upload_binary(
            captions_path, os.path.relpath(captions_path, local_artifacts.OUTPUT_ROOT).replace(os.sep, "/")
        )
        print(f"[Local/Supabase] {source_stem}: removed {removed} boilerplate entries from captions.json")

        # Keep 00_status.json's chunking-stage diagram_count honest too.
        status_path = os.path.join(chapters_root, source_stem, "00_status.json")
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
        if "chunking" in status.get("stages", {}):
            status["stages"]["chunking"]["diagram_count"] = len(kept)
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
        supabase_artifacts.upload_binary(
            status_path, os.path.relpath(status_path, local_artifacts.OUTPUT_ROOT).replace(os.sep, "/")
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually delete from Qdrant/local+Supabase (default: dry run only)")
    args = parser.parse_args()

    proposals = collect_proposals(args.class_name, args.subject)
    print_proposals(proposals)

    if not args.apply:
        print("\n(Dry run only - re-run with --apply to delete these from Qdrant/local+Supabase.)")
        return

    if not proposals["to_delete"]:
        print("\nNothing to delete.")
        return

    apply_deletions(args.class_name, args.subject, proposals["to_delete"])
    print("\nDone.")


if __name__ == "__main__":
    main()
