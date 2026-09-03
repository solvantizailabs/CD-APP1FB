"""
Standalone Hyperframes compile service.

Runs on its own Render web service, separate from the main app, so a burst of
concurrent video-generation requests can't starve the chat/RAG API for CPU or
memory. Talks to the main app over HTTP only - see POST /compile below.

Reuses backend/app/services/visual_learning/hyperframes_engine_bridge.py
unchanged (same compile logic, same Node.js engine, same Supabase upload) so
there is exactly one copy of the actual Hyperframes generation code to
maintain, rather than a forked duplicate that can drift out of sync.
"""

import os
import json
import shutil
import logging

from dotenv import load_dotenv

# Load repo-root .env for local runs (SUPABASE_URL/SUPABASE_KEY etc.) - on
# Render these are set directly in the dashboard instead, and this is a no-op
# if no .env file is present.
SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SERVICE_DIR, ".."))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict

from backend.app.services.visual_learning.hyperframes_engine_bridge import compile_hyperframes_html_fast

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORK_DIR = os.path.join(SERVICE_DIR, "_work")

app = FastAPI(title="Hyperframes Compile Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CompileRequest(BaseModel):
    lesson_id: str
    lesson_package: Dict[str, Any]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/compile")
async def compile_lesson(req: CompileRequest):
    lesson_dir = os.path.join(WORK_DIR, req.lesson_id)
    os.makedirs(lesson_dir, exist_ok=True)

    lesson_json_path = os.path.join(lesson_dir, "lesson.json")
    with open(lesson_json_path, "w", encoding="utf-8") as f:
        json.dump(req.lesson_package, f, indent=2)

    try:
        result = await compile_hyperframes_html_fast(req.lesson_id, lesson_dir)
    finally:
        # This machine's disk is only scratch space for the compile step - the
        # result (if any) is already uploaded to Supabase inside
        # compile_hyperframes_html_fast, so nothing here needs to survive
        # past this request. The caller (main app) never sees this disk.
        shutil.rmtree(lesson_dir, ignore_errors=True)

    if not result:
        return {"url": None, "engine": None, "degraded_reason": "compile_failed", "cloud_url": None}
    return result
