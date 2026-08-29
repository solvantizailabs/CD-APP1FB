"""
Read-only: for a given (class_name, subject, document), list every chunk
whose learning_objective is empty/None, with enough fields to diagnose why
(chunk_type, topic, section, chapter_number, page_number, text snippet).

Usage:
    python terminal_test/inspect_empty_learning_objectives.py --class 10 --subject science
"""
import argparse
import os
import sys

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from qdrant_client import QdrantClient  # noqa: E402

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


def is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="class_name", required=True)
    ap.add_argument("--subject", required=True)
    args = ap.parse_args()

    client = get_client()
    offset = None
    rows = []
    type_counts = {}

    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=SCROLL_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            if str(payload.get("class_name")) != args.class_name or payload.get("subject") != args.subject:
                continue
            if not is_empty(payload.get("learning_objective")):
                continue
            chunk_type = payload.get("chunk_type", "?")
            type_counts[chunk_type] = type_counts.get(chunk_type, 0) + 1
            text = (payload.get("content") or payload.get("text") or "")[:120].replace("\n", " ")
            rows.append({
                "chunk_id": p.id,
                "chunk_type": chunk_type,
                "topic": payload.get("topic"),
                "section": payload.get("section"),
                "chapter_number": payload.get("chapter_number"),
                "page_number": payload.get("page_number"),
                "document_name": payload.get("document_name"),
                "text_snippet": text,
            })
        if offset is None:
            break

    print(f"Empty learning_objective count: {len(rows)}\n")
    print("chunk_type breakdown:")
    for ct, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {ct}: {count}")
    print()

    for r in rows:
        print("-" * 100)
        for k, v in r.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
