"""
Live spot-check of Qdrant `textbooks_v3` for every Class 10 Maths chapter.
Compares live Qdrant point counts (by chapter_id) against the ingestion-time
book_index.json claims, and pulls one real point per chapter to sanity-check
that dense/sparse vectors and payload fields are actually populated.

Run: python -m terminal_test.spot_check_class10_maths
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from qdrant_client import models
from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME

BASE = Path(__file__).resolve().parents[1] / "backend/app/services/new_rag/outputs/Class10_maths"
BOOK_INDEX = BASE / "book_index.json"
CHAPTERS_DIR = BASE / "chapters"


def main():
    client = get_qdrant_client()

    if not client.collection_exists(collection_name=COLLECTION_NAME):
        print(f"COLLECTION '{COLLECTION_NAME}' DOES NOT EXIST. Nothing to check.")
        return

    coll_info = client.get_collection(collection_name=COLLECTION_NAME)
    print(f"Collection '{COLLECTION_NAME}': total points = {coll_info.points_count}\n")

    book_index = json.loads(BOOK_INDEX.read_text(encoding="utf-8"))

    print(f"{'chapter':10} {'name':45} {'claimed':>8} {'live':>6} {'match':>6}")
    print("-" * 80)
    total_claimed = 0
    total_live = 0
    problems = []
    sample_point = None

    for stem, meta in book_index.items():
        status_path = CHAPTERS_DIR / stem / "00_status.json"
        if not status_path.exists():
            problems.append(f"{stem}: no 00_status.json found on disk")
            continue
        status = json.loads(status_path.read_text(encoding="utf-8"))
        book_uuid = status["book_uuid"]
        chapter_id = status["chapter_id"]

        claimed = int(meta["message"].split("Ingested ")[1].split(" chunks")[0])

        count_res = client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid)),
                    models.FieldCondition(key="chapter_id", match=models.MatchValue(value=chapter_id)),
                ]
            ),
            exact=True,
        )
        live = count_res.count
        total_claimed += claimed
        total_live += live
        match = "OK" if live == claimed else "MISMATCH"
        if live != claimed:
            problems.append(f"{stem}: claimed {claimed} chunks at ingestion, {live} live in Qdrant now")
        if live == 0:
            problems.append(f"{stem}: ZERO live points for book_uuid={book_uuid} chapter_id={chapter_id}")

        print(f"{stem:10} {meta['chapter_name'][:45]:45} {claimed:8} {live:6} {match:>8}")

        if sample_point is None and live > 0:
            pts, _ = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid)),
                        models.FieldCondition(key="chapter_id", match=models.MatchValue(value=chapter_id)),
                    ]
                ),
                limit=1,
                with_payload=True,
                with_vectors=True,
            )
            if pts:
                sample_point = (stem, pts[0])

    print("-" * 80)
    print(f"{'TOTAL':10} {'':45} {total_claimed:8} {total_live:6}")

    print("\n=== Sample point sanity check ===")
    if sample_point:
        stem, pt = sample_point
        print(f"From chapter: {stem}  point id: {pt.id}")
        dense = pt.vector.get("dense") if isinstance(pt.vector, dict) else None
        sparse = pt.vector.get("sparse") if isinstance(pt.vector, dict) else None
        print(f"dense vector: len={len(dense) if dense else 0}, "
              f"first5={dense[:5] if dense else None}, "
              f"all_zero={all(v == 0 for v in dense) if dense else 'N/A'}")
        if sparse is not None:
            n_sparse = len(sparse.indices) if hasattr(sparse, 'indices') else len(sparse.get('indices', []))
            print(f"sparse vector: nnz={n_sparse}")
        else:
            print("sparse vector: MISSING")
        payload = pt.payload or {}
        for key in ["content", "book", "class_name", "subject", "chunk_type", "chapter_id",
                    "topic_id", "parent_chunk_id", "book_uuid", "document_name"]:
            val = payload.get(key)
            preview = (val[:100] + "...") if isinstance(val, str) and len(val) > 100 else val
            print(f"  {key}: {preview!r}")
    else:
        print("No live points found in any chapter to sample!")

    print("\n=== Problems found ===")
    if problems:
        for p in problems:
            print(f" - {p}")
    else:
        print("None. All 14 chapters match claimed counts and have live points.")


if __name__ == "__main__":
    main()
