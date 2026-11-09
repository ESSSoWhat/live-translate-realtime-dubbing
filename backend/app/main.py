"""FastAPI application factory."""

from contextlib import asynccontextmanager

import structlog  # type: ignore[import-not-found]
from fastapi import FastAPI, HTTPException  # pyright: ignore[reportMissingImports]
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import (  # type: ignore[import-not-found]  # pylint: disable=import-error
    Limiter,
    _rate_limit_exceeded_handler,
)
from slowapi.errors import (
    RateLimitExceeded,  # type: ignore[import-not-found]  # pylint: disable=import-error
)
from slowapi.util import (
    get_remote_address,  # type: ignore[import-not-found]  # pylint: disable=import-error
)

from app.config import get_settings
from app.routers import analytics, auth, billing, paypal, proxy, user, voice
from app.services.supabase_client import SupabaseNotConfiguredError

logger = structlog.get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    cfg = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.routers.billing import active_wix_tier_mapping

        logger.info("Live Translate API starting", env=cfg.backend_env)
        logger.info(
            "Wix plan->tier mapping active",
            wix_sync_configured=bool((cfg.lt_sync_secret or "").strip()),
            stripe_configured=bool((cfg.stripe_secret_key or "").strip()),
            **active_wix_tier_mapping(),
        )
        yield

    application = FastAPI(
        title="Live Translate API",
        version="1.0.0",
        docs_url=None if cfg.is_production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
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

    # Log unexpected errors. Do not catch HTTPException / RequestValidationError — they subclass
    # Exception and would otherwise be turned into misleading 500 responses.
    async def debug_exception_handler(request, exc: Exception):
        if isinstance(exc, HTTPException):
            return await http_exception_handler(request, exc)
        if isinstance(exc, RequestValidationError):
            return await request_validation_exception_handler(request, exc)
        import traceback

        from fastapi.exceptions import ResponseValidationError

        logger.error("Unhandled exception", error=str(exc), tb=traceback.format_exc())
        if isinstance(exc, ResponseValidationError):
            return JSONResponse(
                status_code=502,
                content={"detail": "Upstream response did not match expected schema"},
            )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    application.add_exception_handler(Exception, debug_exception_handler)

    # CORS (desktop app uses custom protocol, but allow localhost for dev)
    # In production, never use allow_origin_regex with allow_credentials; require explicit allowlist.
    cors_origins = cfg.backend_cors_origins or ""
    if cfg.is_production and (not cors_origins.strip() or cors_origins.strip() == "*"):
        origins: list[str] = []
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
    application.include_router(paypal.router, prefix=prefix)
    application.include_router(voice.router, prefix=prefix)
    application.include_router(analytics.router, prefix=prefix)

    @application.get("/")
    async def root() -> dict:
        return {"message": "Live Translate API", "docs": "/docs", "health": "/health"}

    @application.get("/health")
    async def health() -> dict:
        # build marker — bump when verifying Railway has the latest image
        return {"status": "ok"}

    return application


app = create_app()
