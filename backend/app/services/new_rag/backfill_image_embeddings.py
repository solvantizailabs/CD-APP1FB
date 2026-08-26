"""
One-off backfill: embeds every already-ingested (real, post-cleanup)
diagram image into Stage 2's image-vector collection (textbook_diagrams_v1)
- see docs/IMAGE_PIPELINE_PLAN.md section 3. Chapters ingested before Stage
2 existed never got their diagrams embedded there; this closes that gap
without re-ingesting anything (no LLM calls, runs entirely on the local
CLIP model).

Usage (dry run by default - only prints what would be embedded):
    python -m backend.app.services.new_rag.backfill_image_embeddings --class 10 --subject social

Apply for real:
    python -m backend.app.services.new_rag.backfill_image_embeddings --class 10 --subject social --apply
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

from backend.app.services.new_rag import local_artifacts


def collect_proposals(class_name: str, subject: str) -> Dict[str, List[Dict]]:
    book_dir = local_artifacts.book_dir(class_name, subject)
    chapters_root = os.path.join(book_dir, "chapters")
    to_embed = []

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
        if not captions:
            continue

        images_dir = os.path.join(chdir, "05_diagrams", "images")
        chunks_with_local_files = []
        for c in captions:
            structured_content = c.get("structured_content", "")
            fname = structured_content.rsplit("/", 1)[-1] if structured_content else ""
            local_path = os.path.join(images_dir, fname)
            if fname and os.path.exists(local_path):
                chunks_with_local_files.append((c, local_path))

        if chunks_with_local_files:
            to_embed.append({
                "source_stem": source_stem,
                "book_uuid": status["book_uuid"],
                "chunks_with_local_files": chunks_with_local_files,
            })

    return {"to_embed": to_embed}


def print_proposals(proposals: Dict[str, List[Dict]]) -> None:
    print("\n=== CHAPTERS TO EMBED (Stage 2 image-vector backfill) ===")
    total = 0
    for r in proposals["to_embed"]:
        n = len(r["chunks_with_local_files"])
        total += n
        print(f"  {r['source_stem']}: {n} images")
    print(f"  TOTAL: {total} images")


def apply_embeddings(class_name: str, subject: str, to_embed: List[Dict]) -> None:
    from PIL import Image
    from backend.app.services.new_rag.embeddings.image_embedding_service import embed_images
    from backend.app.services.new_rag.indexing.image_indexer import get_qdrant_client, upsert_diagram_images

    client = get_qdrant_client()
    total_upserted = 0
    for r in to_embed:
        pairs = r["chunks_with_local_files"]
        chunks = [c for c, _ in pairs]
        images = [Image.open(path).convert("RGB") for _, path in pairs]
        vectors = embed_images(images)
        n = upsert_diagram_images(client, chunks, vectors, r["book_uuid"], class_name, subject)
        total_upserted += n
        print(f"  {r['source_stem']}: embedded+upserted {n}")
    print(f"[ImageIndex] Total upserted: {total_upserted}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    proposals = collect_proposals(args.class_name, args.subject)
    print_proposals(proposals)

    if not args.apply:
        print("\n(Dry run only - re-run with --apply to embed and upsert these.)")
        return
    if not proposals["to_embed"]:
        print("\nNothing to embed.")
        return

    apply_embeddings(args.class_name, args.subject, proposals["to_embed"])
    print("\nDone.")


if __name__ == "__main__":
    main()
