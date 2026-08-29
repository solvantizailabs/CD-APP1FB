"""
Read-only Qdrant inspection: for each already-ingested book (class_name +
subject), sample chunk payloads and report whether `learning_objective`
(chunker.py's metadata_fields()) actually came back populated, vs. None/empty.

Chunk data lives in Qdrant, not Firestore - Firestore only holds ingestion
job bookkeeping. Reads the same collection the live app reads from
(NEW_RAG_COLLECTION_NAME env var, default "textbooks_v3" - see
qdrant_indexer.py's module docstring: v3 is the active collection, v2 is
legacy/unused by this pipeline).

Usage:
    python terminal_test/check_learning_objectives.py
"""
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from qdrant_client import QdrantClient, models  # noqa: E402

COLLECTION_NAME = os.environ.get("NEW_RAG_COLLECTION_NAME", "textbooks_v3")
SCROLL_BATCH = 256


def get_client() -> QdrantClient:
    url = os.getenv("QDRANT_URL")
    api_key = os.getenv("QDRANT_API_KEY")
    if not url:
        sys.exit("QDRANT_URL not set in .env")
    kwargs = {"timeout": 60}
    if api_key:
        kwargs["api_key"] = api_key
    if "qdrant.io" in url or "cloud" in url:
        url = url.replace(":6333", "").replace(":6334", "")
        kwargs["port"] = 443
        kwargs["prefer_grpc"] = False
    return QdrantClient(url=url, **kwargs)


def scroll_all(client: QdrantClient):
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=SCROLL_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            yield p
        if offset is None:
            break


def is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def main():
    client = get_client()
    if not client.collection_exists(collection_name=COLLECTION_NAME):
        sys.exit(f"Collection '{COLLECTION_NAME}' does not exist on this Qdrant instance.")

    # book_key -> {"total": int, "populated": int, "empty": int, "examples_missing": [chunk_id,...]}
    stats = defaultdict(lambda: {"total": 0, "populated": 0, "empty": 0, "missing_examples": []})

    for point in scroll_all(client):
        payload = point.payload or {}
        class_name = payload.get("class_name", "?")
        subject = payload.get("subject", "?")
        document_name = payload.get("document_name") or payload.get("document_id", "?")
        key = (class_name, subject, document_name)

        s = stats[key]
        s["total"] += 1
        lo = payload.get("learning_objective")
        if is_empty(lo):
            s["empty"] += 1
            if len(s["missing_examples"]) < 3:
                s["missing_examples"].append(point.id)
        else:
            s["populated"] += 1

    if not stats:
        print(f"No points found in collection '{COLLECTION_NAME}'.")
        return

    print(f"Collection: {COLLECTION_NAME}\n")
    header = f"{'Class':<8}{'Subject':<12}{'Document':<30}{'Total':>7}{'Populated':>11}{'Empty':>8}{'% Populated':>13}"
    print(header)
    print("-" * len(header))
    for (class_name, subject, document_name), s in sorted(stats.items()):
        pct = (s["populated"] / s["total"] * 100) if s["total"] else 0
        doc_short = (document_name or "?")[:28]
        print(f"{class_name:<8}{subject:<12}{doc_short:<30}{s['total']:>7}{s['populated']:>11}{s['empty']:>8}{pct:>12.1f}%")
        if s["empty"]:
            print(f"    example chunk_ids with empty learning_objective: {s['missing_examples']}")

    print()
    total_all = sum(s["total"] for s in stats.values())
    populated_all = sum(s["populated"] for s in stats.values())
    print(f"Overall: {populated_all}/{total_all} chunks have a non-empty learning_objective "
          f"({populated_all / total_all * 100:.1f}%)" if total_all else "Overall: no chunks found")


if __name__ == "__main__":
    main()
