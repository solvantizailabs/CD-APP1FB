FROM python:3.11-slim

# Force unbuffered Python stdout/stderr for real-time log streaming on Render
ENV PYTHONUNBUFFERED=1

# Install system build dependencies. Node.js is no longer installed here -
# Hyperframes (the only thing that needed it) now runs on its own service,
# see hyperframes_service/Dockerfile.
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirement manifest and install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download fastembed model weights during build so server boots instantly (0s startup delay)
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')"

# Copy application source code
COPY . .

# Expose Render standard port 10000
EXPOSE 10000

# Launch FastAPI using dynamic $PORT (defaulting to 10000 for Render)
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
