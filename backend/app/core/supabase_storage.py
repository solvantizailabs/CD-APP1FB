"""
Supabase Cloud Storage Integration for CHADUVU-GURU
Provides 100% free cloud asset storage (videos, audio, lesson JSON) without requiring credit cards or paid cloud plans.
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

def get_supabase_config():
    url = (os.getenv("SUPABASE_URL") or "https://oovmwkwsujujkvmoyffa.supabase.co").strip().rstrip("/")
    key = (os.getenv("SUPABASE_KEY") or "").strip()
    return url, key

BUCKET_NAME = "visual-lessons"

def _ensure_bucket_exists(client: httpx.Client, headers: dict, bucket: str = BUCKET_NAME):
    """Ensure the public storage bucket exists on Supabase."""
    supabase_url, _ = get_supabase_config()
    try:
        bucket_url = f"{supabase_url}/storage/v1/bucket"
        res = client.get(f"{bucket_url}/{bucket}", headers=headers)
        if res.status_code != 200:
            client.post(bucket_url, headers=headers, json={"id": bucket, "name": bucket, "public": True})
    except Exception as e:
        logger.warning(f"[Supabase Storage] Notice ensuring bucket: {e}")

def upload_file_to_supabase(local_path: str, destination_path: str, bucket: str = BUCKET_NAME) -> str:
    """
    Uploads a local file to Supabase Cloud Storage.
    Returns public HTTPS CDN URL on success, or None on fallback.
    Outputs clear diagnostic logs for Render console monitoring.

    `bucket` defaults to the original visual-lessons bucket so every existing
    caller is unaffected; the HyperFrame worker passes bucket="videos" for MP4
    uploads (DronaX - DigitalOcean Platform.pdf, Part G).
    """
    supabase_url, supabase_key = get_supabase_config()
    if not (supabase_url and supabase_key and os.path.exists(local_path)):
        return None

    destination_path = destination_path.lstrip("/")
    url = f"{supabase_url}/storage/v1/object/{bucket}/{destination_path}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apiKey": supabase_key,
        "x-upsert": "true"
    }
    
    # Infer Content-Type
    content_type = "application/octet-stream"
    if local_path.endswith(".html"):
        content_type = "text/html"
    elif local_path.endswith(".wav"):
        content_type = "audio/wav"
    elif local_path.endswith(".json"):
        content_type = "application/json"
    elif local_path.endswith(".js"):
        content_type = "application/javascript"
    elif local_path.endswith(".css"):
        content_type = "text/css"
    elif local_path.endswith(".mp4"):
        content_type = "video/mp4"

    headers["Content-Type"] = content_type
    
    try:
        with open(local_path, "rb") as f:
            content = f.read()
            
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, content=content)
            
            if resp.status_code in [400, 404] and "not found" in resp.text.lower():
                _ensure_bucket_exists(client, headers, bucket)
                resp = client.post(url, headers=headers, content=content)

            if resp.status_code in [200, 201]:
                public_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{destination_path}"
                logger.info(f"[SUPABASE STORAGE SUCCESS] Uploaded {os.path.basename(local_path)} -> {public_url}")
                try:
                    print(f"[RENDER LOG] [SUPABASE STORAGE SUCCESS] Uploaded {os.path.basename(local_path)} -> {public_url}")
                except Exception:
                    pass
                return public_url
            else:
                logger.warning(f"[Supabase Storage Notice] Status {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        logger.warning(f"[Supabase Storage Exception] Could not upload {os.path.basename(local_path)}: {e}")
        return None
