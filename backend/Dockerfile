FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Non-root user for running the app
RUN groupadd --gid 1000 appgroup && useradd --uid 1000 --gid appgroup --shell /bin/false appuser \
    && chown -R appuser:appgroup /app

USER appuser

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
