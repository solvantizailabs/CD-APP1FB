FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
# processor.py imports backend.app.services.visual_learning.* while running
# with cwd=/app/hyperframe-worker (see entrypoint.sh) - PYTHONPATH makes
# /app importable regardless of cwd.
ENV PYTHONPATH=/app

# System deps:
# - Node.js: runs run-storyboard.js (compile) and the `hyperframes` CLI (render)
# - ffmpeg: hyperframes CLI shells out to it to mux captured frames + audio
# - Chrome headless shell's runtime libs (standard Puppeteer/Chromium
#   dependency list on Debian slim - without these the browser binary fails
#   to launch at all, not just renders badly)
# - curl: entrypoint.sh's health check against the internal compile service
# - unzip: THE actual root cause of the earlier "stuck at 100%" build hang -
#   confirmed by a real build log. It wasn't hanging at all - the Chrome
#   Headless Shell download was completing every time, then failing to
#   extract ("no zip archiver is available. Install `unzip`") and silently
#   retrying instead of surfacing that error clearly, which is what made it
#   look like an infinite hang. python:3.11-slim doesn't include unzip.
# Node 22, not 20: the `hyperframes` render CLI hard-requires Node >= 22
# (confirmed by a real local build failure - "HyperFrames requires Node.js
# >= 22 (current: 20.20.2)"). run-storyboard.js (the compile step, shared
# with hyperframes_service which stays on Node 20) doesn't need 22, but
# since this image runs both, it needs the higher of the two requirements.
RUN apt-get update && apt-get install -y \
    curl gnupg ffmpeg unzip \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libpangocairo-1.0-0 fonts-liberation \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Pre-install the `hyperframes` render CLI and its Chrome Headless Shell
# up front, before any application code is copied in. This step alone has
# repeatedly taken minutes (npm registry + a ~114MB Chrome download) and has
# nothing to do with this app's own code - putting it first means iterating
# on Python/JS application code below never invalidates or re-runs it.
#
# CI=true / TERM=dumb / NO_COLOR=1: this CLI uses an interactive clack-style
# animated progress renderer for both install and `browser ensure`, which can
# hang indefinitely inside `docker build` (no real TTY for RUN steps) -
# confirmed by hand, repeatedly, including once on plain `npx hyperframes
# --version`. These force it into non-interactive output.
ENV CI=true
ENV TERM=dumb
ENV NO_COLOR=1
# `timeout -k 15 300`: plain `timeout 300` (SIGTERM only) was itself
# confirmed hanging past its own deadline - npm (or a stuck native-binary
# postinstall child) was ignoring SIGTERM. `-k 15` force-SIGKILLs 15s later
# if still alive. npm's own fetch-timeout/retries are shortened too, so a
# slow/stuck registry connection fails fast on its own.
RUN timeout -k 15 300 npm install -g hyperframes@0.8.27 \
    --fetch-timeout=60000 --fetch-retries=1 --fetch-retry-maxtimeout=60000
RUN timeout -k 15 300 hyperframes browser ensure \
    || timeout -k 15 300 hyperframes browser ensure --force

# --- Python deps ---
COPY hyperframe-worker/requirements.txt ./hyperframe-worker/requirements.txt
RUN pip install --no-cache-dir -r hyperframe-worker/requirements.txt

# --- Reused, unmodified backend code (same folder-depth trick as
# hyperframes_service/Dockerfile, so hyperframes_engine_bridge.py's
# PROJECT_ROOT path math needs no changes) ---
COPY backend/__init__.py backend/__init__.py
COPY backend/app/__init__.py backend/app/__init__.py
# The following list is the result of statically tracing every backend.*
# import reachable from generate_visual_lesson_stream (the worker's actual
# entry point) - 35 files total, done once with an AST walk rather than
# discovering each one via a separate failed build (the first few WERE found
# that way: new_rag and prompts each cost a full rebuild cycle before this
# trace was run). Re-run the same trace if this list ever needs updating:
#   python -c "<AST-based backend.* import walker from visual_learning_service.py>"
COPY backend/app/core/__init__.py backend/app/core/__init__.py
COPY backend/app/core/supabase_storage.py backend/app/core/supabase_storage.py
COPY backend/app/core/video_jobs_firestore.py backend/app/core/video_jobs_firestore.py
COPY backend/app/core/redis_service.py backend/app/core/redis_service.py
COPY backend/app/core/firebase/ backend/app/core/firebase/
COPY backend/app/services/__init__.py backend/app/services/__init__.py
COPY backend/app/services/llm/ backend/app/services/llm/
COPY backend/app/services/retrieval/ backend/app/services/retrieval/
COPY backend/app/services/visual_learning/ backend/app/services/visual_learning/
# new_rag_adapter.py (in retrieval/ above) imports from here. outputs/
# test_outputs (22MB of debug data, not code) are excluded via .dockerignore.
COPY backend/app/services/new_rag/ backend/app/services/new_rag/
COPY backend/app/prompts/ backend/app/prompts/
# chat/__init__.py is empty (confirmed) - copying just tts_service.py avoids
# pulling in the rest of chat.py's much heavier machinery (session_service,
# intent_classifier, etc.) which nothing on this call path actually needs.
COPY backend/app/services/chat/__init__.py backend/app/services/chat/__init__.py
COPY backend/app/services/chat/tts_service.py backend/app/services/chat/tts_service.py
COPY backend/app/utils/ backend/app/utils/

# --- The compile service this worker runs internally on localhost:8001 ---
COPY hyperframes_service/ hyperframes_service/
RUN pip install --no-cache-dir -r hyperframes_service/requirements.txt

# --- The Hyperframes engine itself (templates, run-storyboard.js compiler) ---
COPY hyperframes_engine/ hyperframes_engine/

# --- This worker's own code (changes most often - kept last on purpose) ---
COPY hyperframe-worker/ hyperframe-worker/
RUN chmod +x hyperframe-worker/entrypoint.sh

CMD ["hyperframe-worker/entrypoint.sh"]
