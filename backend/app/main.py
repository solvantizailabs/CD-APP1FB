import os
import sys
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

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
from fastapi.responses import FileResponse, RedirectResponse
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
    history_router
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


@app.api_route("/uploads/{file_path:path}", methods=["GET", "HEAD"])
async def serve_upload_file(file_path: str):
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
    if file_path.startswith(VISUAL_LESSONS_PREFIX):
        from backend.app.core.supabase_storage import get_supabase_config, BUCKET_NAME
        supabase_path = file_path[len(VISUAL_LESSONS_PREFIX):]
        supabase_url, _ = get_supabase_config()
        cloud_url = f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{supabase_path}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                head_resp = await client.head(cloud_url)
            if head_resp.status_code == 200:
                return RedirectResponse(url=cloud_url)
        except Exception as e:
            print(f"[Uploads] Supabase fallback check failed for {file_path}: {e}")

    raise HTTPException(status_code=404, detail="File not found")

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
