"""
Full wipe of one class+subject's data across every system it's stored in -
Qdrant (both textbooks_v3 and textbook_diagrams_v1), Firestore, Supabase
Storage, and local disk. Built for a clean-slate re-ingestion test: leaves
nothing behind that could look like a duplicate or stale leftover from a
previous ingestion run.

Deliberately NOT dry-run-by-default like the other backfill_*.py scripts in
this package - this is a pure deletion tool with no "proposed changes" to
preview beyond point/file counts, and it's meant to be run only after an
explicit, already-confirmed decision to wipe a specific class+subject.
--apply is still required to actually delete anything; without it, only
counts are printed.

Usage:
    python -m backend.app.services.new_rag.wipe_book_data --class 10 --subject social --apply
"""
import argparse
import os
import shutil
import sys
import uuid
from typing import List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()


def _book_uuid(class_name: str, subject: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{class_name}_{subject}".lower()))


def _list_supabase_files(prefix: str) -> List[str]:
    """Recursively lists every file path under prefix in the book-processing
    bucket - the Storage list API only returns one level per call, folders
    included as entries with id=None, so this walks them."""
    import httpx
    from backend.app.core.supabase_storage import get_supabase_config

    supabase_url, supabase_key = get_supabase_config()
    headers = {"Authorization": f"Bearer {supabase_key}", "apiKey": supabase_key}
    files: List[str] = []

    def _walk(path: str, client: "httpx.Client"):
        resp = client.post(
            f"{supabase_url}/storage/v1/object/list/book-processing",
            headers=headers, json={"prefix": path, "limit": 1000},
        )
        if resp.status_code != 200:
            print(f"  [Supabase] list error on {path!r}: {resp.status_code} {resp.text[:200]}")
            return
        for e in resp.json():
            full_path = f"{path}/{e['name']}" if path else e["name"]
            if e.get("id") is None:
                _walk(full_path, client)
            else:
                files.append(full_path)

    with httpx.Client(timeout=30.0) as client:
        _walk(prefix, client)
    return files


def count_all(class_name: str, subject: str) -> dict:
    from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME
    from backend.app.services.new_rag.indexing.image_indexer import IMAGE_COLLECTION_NAME
    from backend.app.core import firestore_service
    from qdrant_client import models

    book_uuid = _book_uuid(class_name, subject)
    client = get_qdrant_client()
    flt = models.Filter(must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))])

    n_text = client.count(collection_name=COLLECTION_NAME, count_filter=flt, exact=True).count
    n_image = 0
    if client.collection_exists(collection_name=IMAGE_COLLECTION_NAME):
        n_image = client.count(collection_name=IMAGE_COLLECTION_NAME, count_filter=flt, exact=True).count

    firestore_doc = firestore_service.load_summary_from_firestore(class_name, subject)
    n_firestore_chapters = len(firestore_doc.get("chapters", [])) if firestore_doc else 0

    prefix = f"Class{class_name}_{subject}"
    supabase_files = _list_supabase_files(prefix)

    local_dir = os.path.join(PROJECT_ROOT, "backend", "app", "services", "new_rag", "outputs", prefix)
    local_file_count = sum(len(files) for _, _, files in os.walk(local_dir)) if os.path.isdir(local_dir) else 0

    return {
        "book_uuid": book_uuid,
        "textbooks_v3_points": n_text,
        "textbook_diagrams_v1_points": n_image,
        "firestore_exists": firestore_doc is not None,
        "firestore_chapters": n_firestore_chapters,
        "supabase_files": len(supabase_files),
        "supabase_file_list": supabase_files,
        "local_dir": local_dir,
        "local_file_count": local_file_count,
    }


def wipe(class_name: str, subject: str, counts: dict) -> None:
    from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, COLLECTION_NAME
    from backend.app.services.new_rag.indexing.image_indexer import IMAGE_COLLECTION_NAME
    from backend.app.core import firestore_service
    from backend.app.core.firebase.firebase_init import db
    from backend.app.services.new_rag import supabase_artifacts
    from qdrant_client import models

    book_uuid = counts["book_uuid"]
    client = get_qdrant_client()
    flt = models.Filter(must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))])

    # 1. Qdrant textbooks_v3
    client.delete(collection_name=COLLECTION_NAME, points_selector=models.FilterSelector(filter=flt))
    print(f"[Qdrant textbooks_v3] Deleted {counts['textbooks_v3_points']} points for book_uuid={book_uuid}")

    # 2. Qdrant textbook_diagrams_v1
    if client.collection_exists(collection_name=IMAGE_COLLECTION_NAME) and counts["textbook_diagrams_v1_points"]:
        client.delete(collection_name=IMAGE_COLLECTION_NAME, points_selector=models.FilterSelector(filter=flt))
        print(f"[Qdrant textbook_diagrams_v1] Deleted {counts['textbook_diagrams_v1_points']} points")

    # 3. Firestore - delete the whole document (classes/{class}/subjects/{subject}).
    clean_class = "".join(c for c in str(class_name) if c.isdigit()) or "unknown"
    doc_ref = db.collection("classes").document(clean_class).collection("subjects").document(subject.strip().lower())
    doc_ref.delete()
    firestore_service.SUMMARY_CACHE.pop(f"{clean_class}_{subject.strip().lower()}", None)
    print(f"[Firestore] Deleted classes/{clean_class}/subjects/{subject.lower()} "
          f"({counts['firestore_chapters']} chapters)")

    # 4. Supabase Storage - bulk delete every file found under the prefix.
    deleted = supabase_artifacts.delete_objects(counts["supabase_file_list"])
    print(f"[Supabase Storage] Requested deletion of {deleted}/{counts['supabase_files']} files")

    # 5. Local disk - remove the whole chapter-outputs folder for this book.
    if os.path.isdir(counts["local_dir"]):
        shutil.rmtree(counts["local_dir"])
        print(f"[Local disk] Removed {counts['local_dir']} ({counts['local_file_count']} files)")
    else:
        print(f"[Local disk] Nothing to remove at {counts['local_dir']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    print(f"\n=== Counting existing data for Class {args.class_name} {args.subject} ===")
    counts = count_all(args.class_name, args.subject)
    print(f"  book_uuid: {counts['book_uuid']}")
    print(f"  textbooks_v3 points: {counts['textbooks_v3_points']}")
    print(f"  textbook_diagrams_v1 points: {counts['textbook_diagrams_v1_points']}")
    print(f"  Firestore: {'exists, ' + str(counts['firestore_chapters']) + ' chapters' if counts['firestore_exists'] else 'not found'}")
    print(f"  Supabase Storage files: {counts['supabase_files']}")
    print(f"  Local disk files: {counts['local_file_count']} at {counts['local_dir']}")

    if not args.apply:
        print("\n(Dry run only - re-run with --apply to actually delete all of the above.)")
        return

    print(f"\n=== WIPING Class {args.class_name} {args.subject} ===")
    wipe(args.class_name, args.subject, counts)
    print("\nDone.")


if __name__ == "__main__":
    main()
