# Root Dockerfile for Google Cloud Build / Cloud Run.
#
# The Cloud Build trigger builds the Dockerfile at the repo root (/workspace/Dockerfile)
# with the repo root as the build context. The FastAPI backend lives under backend/,
# so we copy from there. (Railway continues to use backend/Dockerfile via railway.json —
# this file does not affect that.)

FROM python:3.11-slim

WORKDIR /app

# Install backend dependencies (separate layer for caching).
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend source so that app.main:app resolves to /app/app/main.py.
COPY backend/ .

# Non-root user for running the app.
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid appgroup --shell /bin/false appuser \
    && chown -R appuser:appgroup /app

USER appuser

# Cloud Run injects $PORT (default 8080). Shell form + exec so $PORT expands and
# uvicorn receives signals as PID 1 for graceful shutdown.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 2
