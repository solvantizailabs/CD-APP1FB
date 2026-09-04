#!/bin/bash
# `wait -n` below is a bashism (waits for whichever of several background
# jobs exits first) - confirmed by a real container failure that this
# image's /bin/sh is dash, which doesn't support -n and exits with "Illegal
# option -n" the instant this script runs. python:3.11-slim (Debian-based)
# ships /bin/bash even though /bin/sh defaults to dash, so this shebang
# change alone fixes it - no new package needed.
# Starts the in-container hyperframes_service (compile step, reused unchanged
# from the existing split - see processor.py's docstring for why) on
# localhost, waits for it to be healthy, then starts the actual worker loop.
# Both run in the same container/Droplet: this worker is meant to be the only
# thing on its Droplet (Part R/S), so co-locating them costs nothing and
# avoids a network dependency on the separately-deployed App Platform
# hyperframes_service.
set -e

export HYPERFRAMES_SERVICE_URL="http://127.0.0.1:8001"

echo "[entrypoint] Starting internal hyperframes_service on :8001..."
uvicorn hyperframes_service.main:app --host 127.0.0.1 --port 8001 &
COMPILE_PID=$!

echo "[entrypoint] Waiting for hyperframes_service health check..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8001/health > /dev/null 2>&1; then
        echo "[entrypoint] hyperframes_service is healthy."
        break
    fi
    sleep 1
done

echo "[entrypoint] Starting HyperFrame worker loop..."
cd /app/hyperframe-worker
python worker.py &
WORKER_PID=$!

# If either process dies, exit so the container (and any orchestrator
# watching it - Docker Compose restart policy, or the Controller's
# heartbeat check) knows this worker is dead rather than half-alive.
wait -n $COMPILE_PID $WORKER_PID
exit $?
