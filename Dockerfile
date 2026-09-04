FROM python:3.11-slim

# Force unbuffered Python stdout/stderr for real-time log streaming on Render
ENV PYTHONUNBUFFERED=1

# python:3.11-slim has no locale configured by default. Confirmed by two
# separate real deploy failures on DigitalOcean App Platform - once on the
# Qdrant client, then AGAIN afterward across multiple unrelated subsystems
# (Firestore history lookup, semantic cache, safety moderation, the LLM call
# itself) all failing with "'ascii' codec can't encode characters" on the
# exact same request. Setting LANG/LC_ALL to C.UTF-8 alone did NOT fix the
# second round - that locale isn't reliably available/generated in this
# minimal Debian image, so Python silently keeps falling back to ASCII
# anyway. PYTHONUTF8=1 is Python's own direct UTF-8 mode (3.7+) - it forces
# UTF-8 everywhere Python makes encoding decisions, independent of whatever
# the OS locale actually resolves to. PYTHONIOENCODING backs up stdout/
# stderr specifically. This is the more reliable fix; keeping LANG/LC_ALL
# too since they don't hurt and may matter for non-Python subprocesses.
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8

# Install system build dependencies. Node.js is no longer installed here -
# Hyperframes (the only thing that needed it) now runs on its own service,
# see hyperframes_service/Dockerfile.
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirement manifest and install Python dependencies
COPY requirements.txt ./

# sentence-transformers (below, for CLIP image search) pulls in PyTorch as a
# dependency. pip's default torch wheel bundles several GB of NVIDIA
# CUDA/cuDNN libraries for GPU acceleration - dead weight here, since this is
# always a CPU-only container (python:3.11-slim, no GPU). Confirmed by hand:
# this single package was 6.45GB of the image. Installing the CPU-only torch
# build FIRST means the next line's sentence-transformers install finds
# torch's version requirement already satisfied and skips reinstalling it -
# same CLIP functionality, ~5GB smaller image.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download fastembed model weights during build so server boots instantly (0s startup delay)
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')"

# Copy application source code
COPY . .

# Expose Render standard port 10000
EXPOSE 10000

# Launch FastAPI using dynamic $PORT (defaulting to 10000 for Render)
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
