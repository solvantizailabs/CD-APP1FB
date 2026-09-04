"""
Wraps the `hyperframes` CLI (npm package by HeyGen - real, verified by hand
against this repo's actual compiled lesson output before this worker was
built: it captures real frames and encodes a real MP4, confirmed via
`npx hyperframes render outputs/<lesson_id> -o out.mp4` reaching 75%+ of a
1755-frame/58s lesson before being cut off by a local test timeout). This is
the "Create MP4" step of Part F - deliberately just a subprocess wrapper, not
a reimplementation, per Part E's "do not rewrite unless necessary".

Confirmed multi-minute render time for a ~1 minute lesson is exactly what
justifies the whole async worker/queue design from the doc, rather than
rendering inline in the API.
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

HYPERFRAMES_ENGINE_DIR = os.getenv("HYPERFRAMES_ENGINE_DIR", "/app/hyperframes_engine")

# Real renders run 2-5+ minutes for a ~1 minute lesson on modest CPU (observed
# directly, not guessed) - generous ceiling so a slow Droplet doesn't get
# killed mid-render, but bounded so one stuck job can't hold a worker forever
# (see Part T / Test 4's crash-recovery requirement).
RENDER_TIMEOUT_SECONDS = int(os.getenv("HYPERFRAME_RENDER_TIMEOUT_S", "900"))


def render_lesson_to_mp4(lesson_id: str) -> str:
    """Renders hyperframes_engine/outputs/<lesson_id>/ (already compiled to
    index.html + audio by the storyboard/compile step) to an MP4 in the same
    directory. Returns the absolute path to the MP4, or raises on failure."""
    lesson_dir_rel = os.path.join("outputs", lesson_id)
    lesson_dir_abs = os.path.join(HYPERFRAMES_ENGINE_DIR, lesson_dir_rel)
    if not os.path.exists(os.path.join(lesson_dir_abs, "index.html")):
        raise FileNotFoundError(f"No compiled index.html for lesson {lesson_id} at {lesson_dir_abs}")

    output_mp4 = os.path.join(lesson_dir_abs, "final.mp4")
    # Call the globally-installed binary directly (npm install -g in the
    # Dockerfile), not `npx` - npx re-resolves/re-verifies the package on
    # every invocation, which is the exact operation confirmed to hang
    # unpredictably inside this container (see Dockerfile comments).
    cmd = ["hyperframes", "render", lesson_dir_rel, "-o", os.path.join(lesson_dir_rel, "final.mp4")]

    logger.info(f"[renderer] Rendering lesson {lesson_id}: {' '.join(cmd)} (cwd={HYPERFRAMES_ENGINE_DIR})")
    result = subprocess.run(
        cmd,
        cwd=HYPERFRAMES_ENGINE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        logger.error(f"[renderer] hyperframes render failed for {lesson_id}: {result.stderr[-4000:]}")
        raise RuntimeError(f"hyperframes render exited {result.returncode}")

    if not os.path.exists(output_mp4):
        raise RuntimeError(f"hyperframes render exited 0 but produced no file at {output_mp4}")

    logger.info(f"[renderer] Render complete: {output_mp4}")
    return output_mp4
