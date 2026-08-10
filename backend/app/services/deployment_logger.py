import os
import json
import logging
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(MAIN_DIR, "..", "..", ".."))
CONSOLIDATED_LOG_DIR = os.path.join(PROJECT_ROOT, "consolidated_deployment_outputs")

def get_chat_log_dir() -> str:
    chat_dir = os.path.join(CONSOLIDATED_LOG_DIR, "chat_modes")
    os.makedirs(chat_dir, exist_ok=True)
    return chat_dir

def get_visual_learning_lesson_dir(lesson_id: str) -> str:
    lesson_dir = os.path.join(CONSOLIDATED_LOG_DIR, "visual_learning", "lessons", lesson_id)
    os.makedirs(lesson_dir, exist_ok=True)
    return lesson_dir

def save_chat_log_background(
    user_query: str,
    subject: str,
    mode: str = "text_to_text",
    session_id: Optional[str] = None,
    retrieved_rag_chunks: Optional[List[Dict[str, Any]]] = None,
    llm_response: Optional[str] = None,
    tts_audio_url: Optional[str] = None,
    execution_time_ms: int = 0,
    uid: Optional[str] = None,
    personalization: Optional[Dict[str, Any]] = None
) -> None:
    """
    Asynchronously write daily append-only JSONL log of chat interactions
    without blocking user request flow.

    `personalization` (personalized_learning.md SS7) is the same
    per-turn decision-trace data that otherwise only prints to server
    stdout as `[PERSONALIZATION TRACE]` - e.g.
    {preference, quadrant, escalation_level, per_student_hits,
    global_cache_hit, grade_relative_difficulty}. Previously this function
    was only called from the older /api/query endpoint, not the live
    /api/smart_query path everything in this project runs through - the
    consolidated log directory went stale as a result. Now also called from
    /api/smart_query (chat.py) with this field populated.
    """
    try:
        chat_dir = get_chat_log_dir()
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = os.path.join(chat_dir, f"{today_str}_user_logs.jsonl")

        log_payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "session_id": session_id or "default_session",
            "uid": uid or "anonymous",
            "mode": mode,
            "user_query": user_query,
            "subject": subject,
            "retrieved_rag_chunks": retrieved_rag_chunks or [],
            "llm_response": llm_response or "",
            "tts_audio_url": tts_audio_url or "",
            "execution_time_ms": execution_time_ms,
            "personalization": personalization or {}
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_payload, ensure_ascii=False) + "\n")

        logger.info(f"[DeploymentLogger] Chat interaction logged to {log_file}")
    except Exception as e:
        logger.error(f"[DeploymentLogger] Error logging chat interaction: {e}")

def save_visual_learning_log_bundle(
    lesson_id: str,
    user_request_data: Dict[str, Any],
    storyboard_data: Optional[Dict[str, Any]] = None,
    source_html_path: Optional[str] = None
) -> None:
    """
    Saves visual learning lesson bundle (user_request.json, storyboard.json, index.html)
    under consolidated_deployment_outputs/visual_learning/lessons/<lesson_id>/
    """
    try:
        lesson_log_dir = get_visual_learning_lesson_dir(lesson_id)

        # 1. Save user_request.json
        req_file = os.path.join(lesson_log_dir, "user_request.json")
        user_request_data["timestamp"] = datetime.utcnow().isoformat() + "Z"
        with open(req_file, "w", encoding="utf-8") as f:
            json.dump(user_request_data, f, indent=2, ensure_ascii=False)

        # 2. Save storyboard.json
        if storyboard_data:
            sb_file = os.path.join(lesson_log_dir, "storyboard.json")
            with open(sb_file, "w", encoding="utf-8") as f:
                json.dump(storyboard_data, f, indent=2, ensure_ascii=False)

        # 3. Save index.html
        if source_html_path and os.path.exists(source_html_path):
            dest_html_path = os.path.join(lesson_log_dir, "index.html")
            shutil.copy2(source_html_path, dest_html_path)

        logger.info(f"[DeploymentLogger] Visual learning lesson bundle saved to {lesson_log_dir}")
    except Exception as e:
        logger.error(f"[DeploymentLogger] Error saving visual learning lesson bundle: {e}")
