import os
import sys
import json
import shutil
import logging
import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Find project root
MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(MAIN_DIR, "..", "..", "..", ".."))

def _compile_index_html_python_fallback(lesson_id: str, lesson_dir: str) -> str:
    """
    Pure Python Hyperframes Master Composition generator.
    Generates standalone index.html without requiring Node.js CLI runtime.
    """
    lesson_json_path = os.path.join(lesson_dir, "lesson.json")
    if not os.path.exists(lesson_json_path):
        return None

    try:
        with open(lesson_json_path, "r", encoding="utf-8") as f:
            lesson_data = json.load(f)

        lesson_title = lesson_data.get("lesson_title", "Visual Storyboard Video")
        scenes = lesson_data.get("scenes", [])
        raw_data_json = json.dumps(scenes)
        compiled_at = datetime.now(timezone.utc).isoformat()

        html_content = f"""<!DOCTYPE html>
<!-- HYPERFRAMES_ENGINE: python-fallback compiled={compiled_at} -->
<html>
<head>
  <meta charset="UTF-8">
  <meta name="hf-engine" content="python-fallback">
  <title>{lesson_title}</title>

  <!-- CSS Fonts -->
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;900&family=Space+Grotesk:wght@400;700&family=Inter:wght@400;500;700;900&family=Cinzel:wght@700&family=Playfair+Display:wght@700&family=Roboto:wght@400;700&display=swap" rel="stylesheet">
  
  <!-- KaTeX for math rendering -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  
  <!-- GSAP for animations -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>

  <style>
    :root {{
      --theme-primary-color: #0f172a;
      --theme-secondary-color: #1e293b;
      --theme-accent-color: #3b82f6;
      --theme-bg-color: #090d16;
      --theme-surface-color: #131b2e;
      --theme-text-color: #ffffff;
      --theme-muted-text-color: rgba(255, 255, 255, 0.7);
      --theme-font-family: Inter, system-ui, sans-serif;
    }}
    
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{
      width: 1280px;
      height: 720px;
      overflow: hidden;
      background: var(--theme-bg-color, #090d16);
      font-family: var(--theme-font-family, 'Inter', system-ui, sans-serif);
      color: var(--theme-text-color, #ffffff);
      -webkit-font-smoothing: antialiased;
    }}
    
    .composition {{
      width: 1280px;
      height: 720px;
      position: relative;
    }}
    
    .scene {{
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0; left: 0;
      z-index: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 40px;
      opacity: 0;
      transform: translateY(16px);
      transition: opacity 0.6s ease, transform 0.6s ease;
    }}

    .scene.active {{
      opacity: 1;
      transform: translateY(0);
    }}

    .scene-title {{
      font-size: 42px;
      font-weight: 800;
      color: #ffffff;
      margin-bottom: 24px;
      text-align: center;
      background: linear-gradient(135deg, #60a5fa 0%, #a78bfa 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}

    .scene-body {{
      font-size: 24px;
      line-height: 1.6;
      color: #e2e8f0;
      text-align: center;
      max-width: 900px;
    }}

    .subtitles-container {{
      display: none !important;
      position: absolute;
      bottom: 40px;
      left: 8%;
      right: 8%;
      text-align: center;
      font-size: 22px;
      font-weight: 700;
      color: #ffffff;
      z-index: 90;
      text-shadow: 0 2px 4px rgba(0,0,0,0.9);
      background: rgba(15, 23, 42, 0.85);
      padding: 12px 24px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.1);
    }}
  </style>
</head>
<body>
  <div class="composition" id="hyperframes-container"></div>
  <div class="subtitles-container" id="subtitles-panel" style="display: none;"></div>

  <script>
    const rawData = {raw_data_json};
    let currentSceneIndex = 0;
    let currentAudio = null;
    let advanceTimer = null;

    function estimateDurationSeconds(teacherScript) {{
      const words = teacherScript ? teacherScript.split(/\\s+/).filter(Boolean).length : 0;
      return Math.max(4.0, words * 0.45 + 1.0);
    }}

    function renderScene(index) {{
      if (index < 0 || index >= rawData.length) return;
      if (currentAudio) {{ currentAudio.pause(); currentAudio = null; }}
      if (advanceTimer) {{ clearTimeout(advanceTimer); advanceTimer = null; }}

      const container = document.getElementById('hyperframes-container');
      const sceneData = rawData[index];
      const templateData = sceneData.template_data || {{}};
      const title = templateData.title || sceneData.metadata?.title || 'Visual Learning';

      container.innerHTML = `
        <div class="scene">
          <h1 class="scene-title">${{title}}</h1>
          <div class="scene-body">${{sceneData.teacher_script || ''}}</div>
        </div>
      `;

      // Class is added on a following frame so the opacity/transform CSS
      // transition has a starting state to animate from.
      requestAnimationFrame(() => {{
        requestAnimationFrame(() => {{
          const sceneEl = container.querySelector('.scene');
          if (sceneEl) sceneEl.classList.add('active');
        }});
      }});

      let advanced = false;
      const goNext = () => {{
        if (advanced) return;
        advanced = true;
        if (advanceTimer) {{ clearTimeout(advanceTimer); advanceTimer = null; }}
        if (index + 1 < rawData.length) renderScene(index + 1);
      }};

      if (sceneData.audio_url) {{
        currentAudio = new Audio(sceneData.audio_url);
        currentAudio.play().catch(err => console.log('Audio playback prevented:', err));
        currentAudio.onended = goNext;
        // Safety net: if audio never fires 'onended' (blocked autoplay, load error),
        // still advance based on an estimated narration duration.
        advanceTimer = setTimeout(goNext, estimateDurationSeconds(sceneData.teacher_script) * 1000);
      }} else {{
        // No audio for this scene at all - advance on an estimated reading duration
        // instead of stalling forever.
        advanceTimer = setTimeout(goNext, estimateDurationSeconds(sceneData.teacher_script) * 1000);
      }}
    }}

    document.addEventListener('DOMContentLoaded', () => {{
      if (rawData.length > 0) renderScene(0);
    }});
  </script>
</body>
</html>"""

        dest_html = os.path.join(lesson_dir, "index.html")
        with open(dest_html, "w", encoding="utf-8") as f:
            f.write(html_content)

        return f"/uploads/visual_lessons/{lesson_id}/index.html"
    except Exception as e:
        logger.error(f"[Python HTML Fallback Compiler Error] {e}")
        return None

async def compile_hyperframes_html_fast(lesson_id: str, lesson_dir: str):
    """
    Fast-path Hyperframes Master Composition generator (< 1 second execution).
    Compiles lesson.json, templates, GSAP engine, camera transforms, and narration sync into index.html.
    Returns html_url relative path for instant browser playback.
    """
    # Defensive normalization: if lesson_dir is a file path (e.g. lesson.json), convert to its parent directory
    if os.path.isfile(lesson_dir):
        lesson_dir = os.path.dirname(lesson_dir)

    hf_dir = os.path.join(PROJECT_ROOT, "hyperframes_engine")
    hf_outputs_dir = os.path.join(hf_dir, "outputs", lesson_id)
    try:
        os.makedirs(hf_outputs_dir, exist_ok=True)
    except Exception as e:
        logger.warning(f"[Hyperframes Bridge] Directory creation notice: {e}")

    # Sync lesson.json & audio files into hyperframes outputs dir
    if os.path.abspath(lesson_dir) != os.path.abspath(hf_outputs_dir):
        for item in os.listdir(lesson_dir):
            s_path = os.path.join(lesson_dir, item)
            d_path = os.path.join(hf_outputs_dir, item)
            if os.path.isfile(s_path):
                try:
                    shutil.copy2(s_path, d_path)
                except Exception:
                    pass

    lesson_json_rel = os.path.join("outputs", lesson_id, "lesson.json")

    # NODE_COMPILE_TIMEOUT_S is deliberately generous (well above the ~0.3s warm-run
    # time) to survive cold starts on constrained hosts (e.g. Render free/small tier
    # doing a cold require() of ~239 engine files). A single fast retry follows a
    # timeout on the first attempt, since a fresh `node` process still benefits from
    # the OS disk cache warmed by the first attempt even though V8 JIT state doesn't
    # carry over between processes.
    NODE_COMPILE_TIMEOUT_S = 22
    NODE_COMPILE_RETRY_TIMEOUT_S = 8

    def _run_node_compiler(timeout_s: float):
        cmd = ["node", "run-storyboard.js", lesson_json_rel, "compile"]
        return subprocess.run(
            cmd, cwd=hf_dir, capture_output=True, text=True,
            encoding='utf-8', errors='replace', shell=False, timeout=timeout_s
        )

    degraded_reason = None
    node_ok = False
    attempts = [NODE_COMPILE_TIMEOUT_S, NODE_COMPILE_RETRY_TIMEOUT_S]
    for attempt_idx, attempt_timeout in enumerate(attempts):
        try:
            res = await asyncio.to_thread(_run_node_compiler, attempt_timeout)
            if res.stdout:
                logger.info(f"[Hyperframes Compiler] stdout:\n{res.stdout.strip()}")
            if res.returncode != 0:
                degraded_reason = "nonzero_exit"
                logger.error(
                    f"[HYPERFRAMES_ENGINE_DEGRADED] reason=nonzero_exit lesson_id={lesson_id} "
                    f"attempt={attempt_idx + 1} exit_code={res.returncode} stderr={res.stderr}"
                )
            else:
                logger.info(f"[Hyperframes Compiler] Compilation succeeded (exit 0)")
                node_ok = True
                degraded_reason = None
            break
        except subprocess.TimeoutExpired:
            degraded_reason = "timeout"
            logger.error(
                f"[HYPERFRAMES_ENGINE_DEGRADED] reason=timeout lesson_id={lesson_id} "
                f"attempt={attempt_idx + 1} timeout_s={attempt_timeout}"
            )
            continue
        except FileNotFoundError as e:
            degraded_reason = "node_missing"
            logger.error(f"[HYPERFRAMES_ENGINE_DEGRADED] reason=node_missing lesson_id={lesson_id} error={e}")
            break
        except Exception as e:
            degraded_reason = "exception"
            logger.error(f"[HYPERFRAMES_ENGINE_DEGRADED] reason=exception lesson_id={lesson_id} error={e}")
            break

    # Copy index.html from compiler output dir to uploads serving dir
    src_html = os.path.join(hf_outputs_dir, "index.html")
    dest_html = os.path.join(lesson_dir, "index.html")
    if node_ok and os.path.exists(src_html):
        try:
            shutil.copy2(src_html, dest_html)
        except Exception as copy_err:
            logger.warning(f"[Hyperframes Bridge] HTML copy warning: {copy_err}")

        hf_shared = os.path.join(hf_dir, "shared")
        if os.path.exists(hf_shared):
            try:
                shutil.copytree(hf_shared, os.path.join(lesson_dir, "shared"), dirs_exist_ok=True)
                logger.info(f"[Hyperframes Bridge] Copied shared/ JS libs to {lesson_dir}")
            except Exception as e:
                logger.warning(f"[Hyperframes Bridge] shared/ copy warning: {e}")

        # Backup index.html to Supabase Cloud Storage
        from backend.app.core.supabase_storage import upload_file_to_supabase
        cloud_html_url = upload_file_to_supabase(dest_html, f"{lesson_id}/index.html")

        # Always serve index.html via FastAPI route to guarantee text/html MIME type rendering in browser iframes
        serving_url = f"/uploads/visual_lessons/{lesson_id}/index.html"
        logger.info(f"[RENDER LOG] [ENGINE SUCCESS] Compiled index.html ready -> {serving_url} (Cloud Backup: {cloud_html_url})")
        return {"url": serving_url, "engine": "node", "degraded_reason": None, "cloud_url": cloud_html_url}

    else:
        # Node compile failed, timed out, or produced no output file - fall back to
        # the pure-Python HTML generator so a lesson is still playable.
        if not node_ok:
            logger.error(
                f"[HYPERFRAMES_ENGINE_DEGRADED] reason={degraded_reason or 'no_output'} "
                f"lesson_id={lesson_id} action=falling_back_to_python_compiler"
            )
        elif not os.path.exists(src_html):
            degraded_reason = degraded_reason or "no_output"
            logger.error(
                f"[HYPERFRAMES_ENGINE_DEGRADED] reason=no_output lesson_id={lesson_id} "
                f"action=falling_back_to_python_compiler (node exited 0 but produced no index.html)"
            )

        fallback_url = _compile_index_html_python_fallback(lesson_id, lesson_dir)
        dest_fallback = os.path.join(lesson_dir, "index.html")
        cloud_html_url = None
        if os.path.exists(dest_fallback):
            from backend.app.core.supabase_storage import upload_file_to_supabase
            cloud_html_url = upload_file_to_supabase(dest_fallback, f"{lesson_id}/index.html")
        serving_url = f"/uploads/visual_lessons/{lesson_id}/index.html"
        logger.info(f"[RENDER LOG] [ENGINE SUCCESS] Python fallback HTML compiled -> {serving_url}")
        return {"url": serving_url, "engine": "python_fallback", "degraded_reason": degraded_reason or "unknown", "cloud_url": cloud_html_url}
