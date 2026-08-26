"""
Production-durable storage for new_rag pipeline artifacts, per the original
locked design (docs/RAG_REDESIGN_PLAN.md, section 7): "Supabase Storage
(new bucket, e.g. book-processing) holds the reusable pipeline artifacts."

Reuses the app's existing, already-working Supabase HTTP integration
(backend/app/core/supabase_storage.py, used in production today by chat.py,
tts_service.py, hyperframes_engine_bridge.py) rather than introducing a
second Supabase client. That module only implements upload - this adds the
missing read half, since retrieval (parent-chunk lookup) needs to read
artifacts back, not just write them once at ingestion.

A dedicated bucket ("book-processing") is used, deliberately separate from
the existing "visual-lessons" bucket that production video/audio/lesson
content lives in - keeps RAG pipeline artifacts (raw textbook content,
never user-facing on their own) architecturally separate from generated
lesson media, even though both currently happen to be public buckets.

Local disk (local_artifacts.py) is NOT replaced by this - both still run.
Local disk stays for the same reason it always has (direct inspectability
during testing); Supabase is what makes the parent-chunk lookup actually
survive a production redeploy, which local disk confirmed does not
(git commit e1bc145).
"""
import json
import logging
import os
from typing import Any, List, Optional

import httpx

from backend.app.core.supabase_storage import get_supabase_config

logger = logging.getLogger(__name__)

BUCKET_NAME = "book-processing"


def _ensure_bucket_exists(client: httpx.Client, headers: dict) -> None:
    supabase_url, _ = get_supabase_config()
    try:
        bucket_url = f"{supabase_url}/storage/v1/bucket"
        res = client.get(f"{bucket_url}/{BUCKET_NAME}", headers=headers)
        if res.status_code != 200:
            client.post(bucket_url, headers=headers, json={"id": BUCKET_NAME, "name": BUCKET_NAME, "public": True})
    except Exception as e:
        logger.warning(f"[NEW_RAG][Supabase] Notice ensuring bucket {BUCKET_NAME!r}: {e}")


def upload_json(data: Any, destination_path: str) -> Optional[str]:
    """
    Uploads a Python object as JSON directly to Supabase Storage (no local
    file round-trip required, unlike the existing upload_file_to_supabase
    which takes a local path - this pipeline's artifacts are already
    in-memory dicts at the point they need to be persisted). Returns the
    public HTTPS URL on success, None on failure - callers should treat a
    None return as "Supabase write failed" and decide whether that's fatal
    for their use case (ingestion should probably still succeed locally
    even if the Supabase write has a transient failure; log and continue).
    """
    supabase_url, supabase_key = get_supabase_config()
    if not (supabase_url and supabase_key):
        logger.warning("[NEW_RAG][Supabase] SUPABASE_URL/SUPABASE_KEY not configured - skipping upload.")
        return None

    destination_path = destination_path.lstrip("/")
    url = f"{supabase_url}/storage/v1/object/{BUCKET_NAME}/{destination_path}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apiKey": supabase_key,
        "x-upsert": "true",
        "Content-Type": "application/json",
    }
    content = json.dumps(data, ensure_ascii=False).encode("utf-8")

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, content=content)
            if resp.status_code in (400, 404) and "not found" in resp.text.lower():
                _ensure_bucket_exists(client, headers)
                resp = client.post(url, headers=headers, content=content)

            if resp.status_code in (200, 201):
                public_url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{destination_path}"
                logger.info(f"[NEW_RAG][Supabase] Uploaded {destination_path} -> {public_url}")
                return public_url
            logger.warning(f"[NEW_RAG][Supabase] Upload failed, status {resp.status_code}: {resp.text[:300]}")
            return None
    except Exception as e:
        logger.warning(f"[NEW_RAG][Supabase] Upload exception for {destination_path}: {e}")
        return None


_CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".json": "application/json", ".md": "text/markdown",
}


def upload_binary(local_path: str, destination_path: str) -> Optional[str]:
    """
    Uploads a local file (diagram images, chapter_overview.md, or any other
    already-on-disk artifact) to Supabase Storage, mirroring the full local
    outputs/ hierarchy so the durability guarantee upload_json() already
    provides for parents_lookup.json extends to every other pipeline
    artifact too (raw pages, manifests, diagrams) - see the integration
    plan's "full artifact mirroring" decision. Content-type inferred from
    extension. Same fail-open contract as upload_json(): returns None (never
    raises) on any failure, so a Supabase hiccup never blocks ingestion -
    the local copy always succeeds independently of this call.
    """
    supabase_url, supabase_key = get_supabase_config()
    if not (supabase_url and supabase_key):
        logger.warning("[NEW_RAG][Supabase] SUPABASE_URL/SUPABASE_KEY not configured - skipping upload.")
        return None
    if not os.path.exists(local_path):
        logger.warning(f"[NEW_RAG][Supabase] Local file not found, skipping upload: {local_path}")
        return None

    destination_path = destination_path.lstrip("/")
    url = f"{supabase_url}/storage/v1/object/{BUCKET_NAME}/{destination_path}"
    ext = os.path.splitext(local_path)[1].lower()
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apiKey": supabase_key,
        "x-upsert": "true",
        "Content-Type": content_type,
    }

    try:
        with open(local_path, "rb") as f:
            content = f.read()
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, content=content)
            if resp.status_code in (400, 404) and "not found" in resp.text.lower():
                _ensure_bucket_exists(client, headers)
                resp = client.post(url, headers=headers, content=content)

            if resp.status_code in (200, 201):
                public_url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{destination_path}"
                logger.info(f"[NEW_RAG][Supabase] Uploaded {destination_path} -> {public_url}")
                return public_url
            logger.warning(f"[NEW_RAG][Supabase] Upload failed, status {resp.status_code}: {resp.text[:300]}")
            return None
    except Exception as e:
        logger.warning(f"[NEW_RAG][Supabase] Upload exception for {destination_path}: {e}")
        return None


def delete_objects(destination_paths: List[str]) -> int:
    """
    Bulk-deletes objects from Supabase Storage via the standard Storage API
    remove endpoint (DELETE /storage/v1/object/{bucket}, body {"prefixes":
    [...]}). Used by the boilerplate-image cleanup: a chunk can be de-linked
    from Qdrant/captions.json without the underlying file ever being
    removed from the bucket, leaving orphaned watermark/icon files behind -
    this is the explicit follow-up step for that. Chunked into batches of
    100 paths per request (a practical batch size for this endpoint, not a
    documented hard limit) so a single call with hundreds of paths doesn't
    risk a request-size/timeout failure losing the whole batch. Fail-open
    per batch, same contract as upload_binary()/upload_json(): a failed
    batch is logged and skipped rather than raising, so one bad batch
    doesn't stop the rest from being cleaned up. Returns the number of
    paths successfully requested for deletion (Supabase's response doesn't
    reliably distinguish "deleted" from "already absent", so this counts
    requested-and-not-errored, not a verified count).
    """
    supabase_url, supabase_key = get_supabase_config()
    if not (supabase_url and supabase_key):
        logger.warning("[NEW_RAG][Supabase] SUPABASE_URL/SUPABASE_KEY not configured - skipping delete.")
        return 0
    if not destination_paths:
        return 0

    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apiKey": supabase_key,
        "Content-Type": "application/json",
    }
    url = f"{supabase_url}/storage/v1/object/{BUCKET_NAME}"
    deleted = 0
    batch_size = 100
    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(destination_paths), batch_size):
            batch = [p.lstrip("/") for p in destination_paths[i:i + batch_size]]
            try:
                resp = client.request("DELETE", url, headers=headers, json={"prefixes": batch})
                if resp.status_code in (200, 204):
                    deleted += len(batch)
                else:
                    logger.warning(f"[NEW_RAG][Supabase] Bulk delete batch failed, status {resp.status_code}: {resp.text[:300]}")
            except Exception as e:
                logger.warning(f"[NEW_RAG][Supabase] Bulk delete batch exception: {e}")
    return deleted


def download_json(destination_path: str) -> Optional[Any]:
    """
    Reads a JSON artifact back from Supabase Storage. The bucket is public
    (same pattern as the existing visual-lessons bucket), so this is a plain
    GET on the public object URL - no signed URL or extra auth needed for
    reads, only for writes. Returns None (not an exception) on any failure -
    callers at retrieval time should treat this as "no parent found",
    the same as today's local-disk lookup already does when the key is
    missing, not as a hard error that should break a query.
    """
    supabase_url, _ = get_supabase_config()
    if not supabase_url:
        return None

    destination_path = destination_path.lstrip("/")
    url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{destination_path}"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code != 404:
                logger.warning(f"[NEW_RAG][Supabase] Download of {destination_path} returned {resp.status_code}")
            return None
    except Exception as e:
        logger.warning(f"[NEW_RAG][Supabase] Download exception for {destination_path}: {e}")
        return None
