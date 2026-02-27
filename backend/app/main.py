"""FastAPI application factory."""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.routers import auth, billing, proxy, user

logger = structlog.get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)


def create_app() -> FastAPI:
    cfg = get_settings()

    app = FastAPI(
        title="Live Translate API",
        version="1.0.0",
        docs_url=None if cfg.is_production else "/docs",
        redoc_url=None,
    )

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS (desktop app uses custom protocol, but allow localhost for dev)
    origins = cfg.backend_cors_origins.split(",") if cfg.backend_cors_origins != "*" else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    prefix = "/api/v1"
    app.include_router(auth.router, prefix=prefix)
    app.include_router(user.router, prefix=prefix)
    app.include_router(proxy.router, prefix=prefix)
    app.include_router(billing.router, prefix=prefix)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("Live Translate API starting", env=cfg.backend_env)

    return app


app = create_app()
