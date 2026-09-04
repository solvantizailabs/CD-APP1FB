"""Uploads the final MP4 to Supabase Storage (Part G). Standalone duplicate of
backend/app/core/supabase_storage.py's upload logic for the same
independent-deployable reason as redis_client.py/database.py - the worker
image doesn't bundle the whole backend/app/core tree for this one function."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

BUCKET_NAME = "videos"


def _config():
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("SUPABASE_KEY") or "").strip()
    return url, key


def _ensure_bucket(client: httpx.Client, headers: dict):
    supabase_url, _ = _config()
    bucket_url = f"{supabase_url}/storage/v1/bucket"
    res = client.get(f"{bucket_url}/{BUCKET_NAME}", headers=headers)
    if res.status_code != 200:
        client.post(bucket_url, headers=headers, json={"id": BUCKET_NAME, "name": BUCKET_NAME, "public": True})


def upload_video(local_mp4_path: str, job_id: str) -> str:
    """final.mp4 -> Supabase Storage -> videos/{job_id}/final.mp4, matching
    Part G exactly. Returns the public URL, or raises on failure (the caller
    marks the job failed - a video that silently has no URL is worse than an
    explicit failure)."""
    supabase_url, supabase_key = _config()
    if not (supabase_url and supabase_key):
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY not configured")
    if not os.path.exists(local_mp4_path):
        raise FileNotFoundError(local_mp4_path)

    destination = f"{job_id}/final.mp4"
    url = f"{supabase_url}/storage/v1/object/{BUCKET_NAME}/{destination}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apiKey": supabase_key,
        "x-upsert": "true",
        "Content-Type": "video/mp4",
    }

    with open(local_mp4_path, "rb") as f:
        content = f.read()

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, content=content)
        if resp.status_code in (400, 404) and "not found" in resp.text.lower():
            _ensure_bucket(client, headers)
            resp = client.post(url, headers=headers, content=content)

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Supabase upload failed: {resp.status_code} {resp.text}")

    public_url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{destination}"
    logger.info(f"[storage] Uploaded {local_mp4_path} -> {public_url}")
    return public_url
