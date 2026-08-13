import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud import storage

# Path to service account
FIREBASE_DIR = os.path.dirname(os.path.abspath(__file__))
SA_PATH = None
curr_dir = FIREBASE_DIR
for _ in range(6):  # check up to 6 parent directories
    temp_path = os.path.join(curr_dir, "serviceAccountKey.json")
    if os.path.exists(temp_path):
        SA_PATH = temp_path
        break
    parent = os.path.dirname(curr_dir)
    if parent == curr_dir:
        break
    curr_dir = parent

if not SA_PATH:
    SA_PATH = os.path.abspath(os.path.join(FIREBASE_DIR, "..", "..", "..", "..", "serviceAccountKey.json"))

firebase_env_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON") or os.environ.get("FIREBASE_CREDENTIALS")

if not firebase_admin._apps:
    if firebase_env_json:
        try:
            cred_dict = json.loads(firebase_env_json)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            print("[Firebase Success] Initialized Firebase Admin from environment variable.")
        except Exception as e:
            print(f"[Firebase Warning] Failed to initialize Firebase from env: {e}")
    elif SA_PATH and os.path.exists(SA_PATH):
        try:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SA_PATH
            cred = credentials.Certificate(SA_PATH)
            firebase_admin.initialize_app(cred)
            print(f"[Firebase Success] Initialized Firebase Admin from file: {SA_PATH}")
        except Exception as e:
            print(f"[Firebase Warning] Failed to initialize Firebase from file: {e}")
    else:
        print("[Firebase Warning] serviceAccountKey.json not found on disk & FIREBASE_SERVICE_ACCOUNT_JSON not set. Firebase Admin SDK skipped.")

class _LazyFirestoreClient:
    """
    Proxies every attribute access to a real firestore.Client, created on
    first successful use rather than once at import time.

    Real incident: `db = firestore.client()` used to run exactly once, at
    process startup. A transient failure right then (e.g. a brief network
    hiccup during a Render cold start) left `db` permanently None for that
    worker's entire lifetime - every request from then on 500'd with
    "'NoneType' object has no attribute 'collection'" until someone
    manually restarted the service. This retries on every access instead,
    so a one-off startup blip self-heals on the very next request instead
    of requiring a restart. No caller does `if db:`/`is None` truthiness
    checks on this (confirmed repo-wide), so swapping the plain value for
    an always-truthy proxy object is safe.
    """
    def __getattr__(self, name):
        client = _get_or_init_firestore_client()
        if client is None:
            raise RuntimeError(
                "Firestore client is unavailable - Firebase Admin failed to "
                "initialize. Check startup logs for the [Firebase Warning] line."
            )
        return getattr(client, name)


_firestore_client = None


def _get_or_init_firestore_client():
    global _firestore_client
    if _firestore_client is not None:
        return _firestore_client
    if not firebase_admin._apps:
        return None
    try:
        _firestore_client = firestore.client()
    except Exception as e:
        print(f"[Firebase Warning] Could not initialize Firestore client (will retry on next access): {e}")
        return None
    return _firestore_client


db = _LazyFirestoreClient()

# Google Cloud Storage / Firebase Storage
GCS_BUCKET = os.getenv("GCS_BUCKET_NAME") or os.getenv("FIREBASE_STORAGE_BUCKET") or "chaduvu-guru.firebasestorage.app"
try:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        gcs_client = storage.Client()
        bucket = gcs_client.bucket(GCS_BUCKET)
    else:
        gcs_client = None
        bucket = None
except Exception as e:
    print(f"[GCS Warning] Cloud storage bucket initialization notice: {e}")
    gcs_client = None
    bucket = None

def upload_file_to_firebase(local_path: str, destination_blob_name: str) -> str:
    """
    Safely uploads a local file to Firebase Storage if bucket exists.
    Returns public CDN URL on success, or None if bucket is unavailable.
    Outputs clear diagnostic logs for Render console monitoring.
    """
    if not (bucket and os.path.exists(local_path)):
        return None
    try:
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(local_path)
        blob.make_public()
        public_url = blob.public_url
        print(f"✅ [RENDER LOG] [FIREBASE STORAGE SUCCESS] Uploaded {os.path.basename(local_path)} -> {public_url}")
        return public_url
    except Exception as upload_err:
        print(f"⚠️ [RENDER LOG] [FIREBASE STORAGE NOTICE] Could not upload {os.path.basename(local_path)} to bucket '{GCS_BUCKET}': {upload_err}")
        return None

