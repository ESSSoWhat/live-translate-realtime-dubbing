"""FastAPI application factory."""

import structlog  # type: ignore[import-not-found]
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore[import-not-found]  # pylint: disable=import-error
from slowapi.errors import RateLimitExceeded  # type: ignore[import-not-found]  # pylint: disable=import-error
from slowapi.util import get_remote_address  # type: ignore[import-not-found]  # pylint: disable=import-error

from app.config import get_settings
from app.routers import auth, billing, proxy, user
from app.services.supabase_client import SupabaseNotConfiguredError

logger = structlog.get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    cfg = get_settings()

    application = FastAPI(
        title="Live Translate API",
        version="1.0.0",
        docs_url=None if cfg.is_production else "/docs",
        redoc_url=None,
    )

    # Rate limiting
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    def supabase_not_configured(_request: object, exc: SupabaseNotConfiguredError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
        )

    application.add_exception_handler(SupabaseNotConfiguredError, supabase_not_configured)

    # CORS (desktop app uses custom protocol, but allow localhost for dev)
    # In production, never use allow_origin_regex with allow_credentials; require explicit allowlist.
    cors_origins = cfg.backend_cors_origins or ""
    if cfg.is_production and (not cors_origins.strip() or cors_origins.strip() == "*"):
        origins = []
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    elif cors_origins.strip() == "*":
        application.add_middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_origin_regex=".*",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        if cfg.is_production and not origins:
            origins = []
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Routers
    prefix = "/api/v1"
    application.include_router(auth.router, prefix=prefix)
    application.include_router(user.router, prefix=prefix)
    application.include_router(proxy.router, prefix=prefix)
    application.include_router(billing.router, prefix=prefix)

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @application.on_event("startup")
    async def on_startup() -> None:
        logger.info("Live Translate API starting", env=cfg.backend_env)

    return application


app = create_app()
