import os
import sys
import asyncio
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# No logging.basicConfig() existed anywhere in this app before 2026-09-02 -
# every logger.info()/warning() call across backend/ (including all of
# question_pipeline/'s stage tracing) silently went nowhere, since Python's
# logging is a no-op without a configured handler. Root stays at WARNING to
# keep third-party libraries (urllib3, openai SDK, etc.) quiet; only this
# app's own "backend.*" loggers are bumped to INFO so pipeline tracing is
# actually visible in the console while testing.
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("backend").setLevel(logging.INFO)

# Found live 2026-09-02: with logging now configured (above), logger.info()
# lines appeared in the console immediately, but the codebase's many raw
# print() calls (session_service.py's "[SESSION] ..." lines, chat.py's
# "[CACHE MISS] ..." lines, etc.) did not - they stayed invisible for the
# entire ~60-90s a real curriculum-decision request takes, making an
# in-progress request look frozen. Root cause: Python's logging.StreamHandler
# explicitly flushes on every emit, but stdout itself is block-buffered by
# default when it isn't a real interactive terminal (piped through an IDE's
# integrated terminal, uvicorn --reload's subprocess, etc.) - print() output
# just sits in that buffer instead of appearing immediately. Forcing line
# buffering makes print() flush after every newline, same as logging already
# does, so console output actually reflects what's happening in real time.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Ensure Windows uses Proactor event loop for subprocess support
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
    try:
        # Windows' default console codepage (cp1252) can't encode most emoji or
        # box-drawing characters, which this codebase prints a lot of (progress
        # bars, checkmarks, status icons). An unencodable print() crashes
        # whatever called it outright - including fire-and-forget background
        # tasks like book ingestion, which then fail silently from the caller's
        # perspective (the HTTP request already returned 200). Reconfiguring
        # stdout/stderr to UTF-8 with substitution on failure fixes this for
        # every print() in the process, everywhere, rather than patching each
        # Unicode character as it's found.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load environment variables FIRST with override to prioritize .env file over system env vars
# Resolve root .env path relative to main.py
MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(MAIN_DIR, "..", ".."))
env_path = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(dotenv_path=env_path, override=True)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from backend.app.services.retrieval import qdrant_service as qdrant
from backend.app.core.auth_middleware import auth_middleware
from backend.app.api.routes import (
    books_router,
    chat_router,
    dashboard_router,
    bag_router,
    profile_router,
    tts_router,
    personalization_router,
    history_router,
    pipeline_logs_router,
    video_router,
)

# --- Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup, initialize all models and database connections
    try:
        qdrant.initialize()
        print("[OK] Qdrant initialized successfully")
    except Exception as e:
        print(f"[WARN] Qdrant initialization failed: {e}")
        print("[WARN] Server will continue without Qdrant (some features may be limited)")
    yield
    # On shutdown

# Initialize FastAPI app with the lifespan manager
app = FastAPI(lifespan=lifespan, title="CHADUVU-GURU API Backend", version="1.0.0")

# Setup CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add authentication middleware
app.middleware("http")(auth_middleware)

# Register routes
app.include_router(books_router)
app.include_router(chat_router)
app.include_router(dashboard_router)
app.include_router(bag_router)
app.include_router(profile_router)
app.include_router(tts_router)
app.include_router(personalization_router)
app.include_router(history_router)
app.include_router(pipeline_logs_router)
app.include_router(video_router)


@app.get("/health")
async def health_check():
    """DigitalOcean App Platform health check target (DronaX - DigitalOcean
    Platform.pdf, Part Q)."""
    return {"status": "healthy"}
# Note: visual_learning_router (the standalone /api/visual_learning HTTP endpoint)
# has been removed - nothing in the frontend calls it anymore (the "Visual
# Learning Mode" UI it served was dead/unreachable code). The underlying
# generate_visual_lesson_stream() function it wrapped is still very much in use -
# it's imported directly by chat.py's orchestrator flow, which is now the only
# caller and the single real video-generation pipeline.

# --- STATIC FILE SERVING ---
PUBLIC_DIR = os.path.join(PROJECT_ROOT, "public")
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "uploads")

try:
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
except Exception as e:
    logger.warning(f"Static directory creation notice: {e}")

# Mount static files directories
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

TMP_UPLOADS_DIR = os.path.join("/tmp", "uploads")

VISUAL_LESSONS_PREFIX = "visual_lessons/"


HYPERFRAMES_SHARED_DIR = os.path.join(PROJECT_ROOT, "hyperframes_engine", "shared")


@app.api_route("/uploads/{file_path:path}", methods=["GET", "HEAD"])
async def serve_upload_file(file_path: str):
    # Real incident: every lesson gets its OWN copy of shared/ (animations.js,
    # theme.js, icons.js, diagram-library.json, template-registry.json) -
    # identical content, physically duplicated per lesson_id - and that
    # per-lesson copy was never included in the Supabase backup at all (only
    # index.html/lesson.json/scene audio were). So once a redeploy wipes local
    # disk, every lesson's scene-sequencing engine 404s, and the browser just
    # renders every scene's static markup at once instead of animating through
    # them one at a time - looks like "the video isn't playing," but it's
    # really "the engine that plays it never loaded."
    # Fix at the source instead of trying to patch the backup: shared/ is
    # genuinely lesson-agnostic (same 5 files for every lesson) and lives in
    # hyperframes_engine/shared/, which is committed to git - always present
    # on every deploy, unlike anything under ephemeral uploads/. Serve
    # visual_lessons/{any_lesson_id}/shared/{file} straight from there,
    # bypassing the whole per-lesson-copy/backup dance entirely.
    if file_path.startswith(VISUAL_LESSONS_PREFIX) and "/shared/" in file_path:
        shared_filename = file_path.split("/shared/", 1)[1]
        shared_path = os.path.join(HYPERFRAMES_SHARED_DIR, shared_filename)
        if os.path.exists(shared_path) and os.path.isfile(shared_path):
            return FileResponse(shared_path)

    local_path = os.path.join(UPLOADS_DIR, file_path)
    if os.path.exists(local_path) and os.path.isfile(local_path):
        return FileResponse(local_path)

    tmp_path = os.path.join(TMP_UPLOADS_DIR, file_path)
    if os.path.exists(tmp_path) and os.path.isfile(tmp_path):
        return FileResponse(tmp_path)

    # Local disk is ephemeral - wiped on redeploy/restart, and not shared
    # across instances if this ever runs behind more than one. Every visual
    # lesson file is ALSO backed up to Supabase at generation time (see
    # hyperframes_engine_bridge.py's upload_file_to_supabase calls), so fall
    # back to that durable copy instead of 404ing when the local one is gone.
    #
    # This MUST be a server-side fetch-and-serve, not a redirect to the
    # Supabase URL directly - real incident: every video generated before a
    # Render restart went black/frozen because Supabase Storage serves public
    # objects with `Content-Security-Policy: default-src 'none'; sandbox`
    # (its own hard security default for publicly-hosted HTML, not something
    # we set), which blocks every script the interactive lesson page needs to
    # run. Fetching it ourselves and re-serving it as our own response with
    # our own headers avoids that CSP entirely, since the browser only ever
    # sees this content as coming from our domain. Also don't trust
    # Supabase's own returned Content-Type for this - it serves .html as
    # text/plain, which stops the browser treating it as a document at all.
    if file_path.startswith(VISUAL_LESSONS_PREFIX):
        from backend.app.core.supabase_storage import get_supabase_config, BUCKET_NAME
        supabase_path = file_path[len(VISUAL_LESSONS_PREFIX):]
        supabase_url, _ = get_supabase_config()
        cloud_url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{supabase_path}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                cloud_resp = await client.get(cloud_url)
            if cloud_resp.status_code == 200:
                return Response(content=cloud_resp.content, media_type=_guess_content_type(file_path))
        except Exception as e:
            print(f"[Uploads] Supabase fallback fetch failed for {file_path}: {e}")

    raise HTTPException(status_code=404, detail="File not found")


def _guess_content_type(file_path: str) -> str:
    """Content-Type by extension - mirrors supabase_storage.py's upload-time
    mapping. Deliberately not trusted from Supabase's own response (see
    serve_upload_file's docstring above) since it serves .html as text/plain."""
    ext_map = {
        ".html": "text/html", ".js": "application/javascript",
        ".css": "text/css", ".json": "application/json",
        ".wav": "audio/wav", ".mp3": "audio/mpeg",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml", ".webp": "image/webp",
    }
    for ext, content_type in ext_map.items():
        if file_path.endswith(ext):
            return content_type
    return "application/octet-stream"

# --- HTML TEMPLATE ROUTING ---
@app.api_route("/", methods=["GET", "HEAD"])
async def read_root():
    return FileResponse(os.path.join(PUBLIC_DIR, 'index.html'))

@app.get("/enhanced-dashboard")
async def enhanced_dashboard_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'enhanced-dashboard.html'))

@app.get("/admin")
async def admin_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'admin.html'))

@app.get("/admin-login")
async def admin_login_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'admin-login.html'))

@app.get("/admin-login.html")
async def admin_login_html_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'admin-login.html'))

@app.get("/admin-dashboard")
async def admin_dashboard_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'admin-dashboard.html'))

@app.get("/admin-dashboard.html")
async def admin_dashboard_html_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'admin-dashboard.html'))

@app.get("/user")
async def user_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'user.html'))

@app.get("/chapters")
async def chapters_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'chapters.html'))

@app.get("/profile")
async def profile_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'profile.html'))

@app.get("/achievements")
async def achievements_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'achievements.html'))

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'dashboard.html'))

@app.get("/pipeline-logs")
async def pipeline_logs_page():
    return FileResponse(
        os.path.join(PUBLIC_DIR, 'pipeline-logs.html'),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )

@app.get("/my-bag")
async def my_bag_page():
    # This page is under active iteration and got mistaken for "the fix
    # didn't apply" twice already because browsers will heuristically cache
    # an HTML document response (no explicit Cache-Control here previously)
    # even though FileResponse always reads the current file from disk on
    # the server side. Force revalidation so a reload always reflects
    # what's actually on disk, not a stale browser copy.
    return FileResponse(
        os.path.join(PUBLIC_DIR, 'my-bag-component.html'),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )

@app.get("/mode-selection")
async def mode_selection_page():
    return FileResponse(os.path.join(PUBLIC_DIR, 'mode-selection.html'))

@app.get("/logout")
async def logout_page():
    """Client-side Firebase logout is handled by JS. This route just redirects back to home."""
    return RedirectResponse(url="/", status_code=302)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(PUBLIC_DIR, 'favicon.ico')
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return FileResponse(os.path.join(PUBLIC_DIR, 'index.html'), status_code=204)
