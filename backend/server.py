"""Supervisor/dev entrypoint: expose the FastAPI app as `server:app`.

Production (Docker/Railway/Procfile) uses `app.main:app` directly; this shim
only exists so the Emergent supervisor command `uvicorn server:app` works.
"""

from app.main import app

__all__ = ["app"]
