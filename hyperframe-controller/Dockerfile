FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
# controller.py imports backend.app.core.video_jobs_firestore while running
# with cwd=/app/hyperframe-controller - PYTHONPATH makes /app importable
# regardless of cwd.
ENV PYTHONPATH=/app

WORKDIR /app
COPY hyperframe-controller/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Needed for the Firestore-based stale-job check (video_jobs tracking lives
# in Firestore, not Postgres - see backend/app/core/video_jobs_firestore.py).
COPY backend/__init__.py backend/__init__.py
COPY backend/app/__init__.py backend/app/__init__.py
COPY backend/app/core/__init__.py backend/app/core/__init__.py
COPY backend/app/core/video_jobs_firestore.py backend/app/core/video_jobs_firestore.py
COPY backend/app/core/firebase/ backend/app/core/firebase/

COPY hyperframe-controller/ ./hyperframe-controller/

WORKDIR /app/hyperframe-controller
CMD ["python", "controller.py"]
