# Redunator Phase 3 backend — Docker image for Render (Worker C).
# See backend/DEPLOY.md. Build context is the REPO ROOT (Render uses
# dockerfilePath: backend/Dockerfile); .dockerignore at the repo root
# keeps the 2.9 GB reference model and the raw ingest files out of
# the image. The image still carries the served models + processed
# data (~200 MB of app files on top of the Python ML base layers).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Single uvicorn worker (the default): rate limiting is in-memory per
# process, and one worker already uses ~1.2 GB warm — do NOT raise
# --workers on the free/512 MB plans (see DEPLOY.md).
EXPOSE 8000

# Render injects $PORT; bind 0.0.0.0 so the platform can reach the app.
CMD ["sh", "-c", "REDUNATOR_HOST=0.0.0.0 REDUNATOR_PORT=${PORT:-8000} python -m backend.app"]
