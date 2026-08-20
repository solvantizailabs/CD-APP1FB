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
from typing import Any, Optional

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
