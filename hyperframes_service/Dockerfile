FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Node.js is needed here (and only here) to run the Hyperframes engine's
# run-storyboard.js compiler - the main app's image no longer installs it.
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY hyperframes_service/requirements.txt ./hyperframes_service/requirements.txt
RUN pip install --no-cache-dir -r hyperframes_service/requirements.txt

# Preserve the same repo-relative folder depth the main app uses
# (backend/app/services/visual_learning/hyperframes_engine_bridge.py sits 4
# directories below the project root) so its PROJECT_ROOT path math still
# resolves to /app/hyperframes_engine without any changes to that file.
COPY backend/__init__.py backend/__init__.py
COPY backend/app/__init__.py backend/app/__init__.py
COPY backend/app/core/__init__.py backend/app/core/__init__.py
COPY backend/app/core/supabase_storage.py backend/app/core/supabase_storage.py
COPY backend/app/services/__init__.py backend/app/services/__init__.py
COPY backend/app/services/visual_learning/__init__.py backend/app/services/visual_learning/__init__.py
COPY backend/app/services/visual_learning/hyperframes_engine_bridge.py backend/app/services/visual_learning/hyperframes_engine_bridge.py

COPY hyperframes_engine/ hyperframes_engine/
COPY hyperframes_service/ hyperframes_service/

EXPOSE 10000

CMD ["sh", "-c", "uvicorn hyperframes_service.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
