"""Supabase client singleton."""

import asyncio

import structlog
from supabase import AsyncClient, acreate_client

from app.config import get_settings

logger = structlog.get_logger(__name__)


class SupabaseNotConfiguredError(Exception):
    """Raised when Supabase URL or service role key is not set."""


_client: AsyncClient | None = None
_client_lock = asyncio.Lock()


async def get_supabase() -> AsyncClient:
    """Return the shared Supabase async client (created on first call)."""
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                cfg = get_settings()
                if not (cfg.supabase_url and cfg.supabase_service_role_key):
                    raise SupabaseNotConfiguredError(
                        "Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env"
                    )
                _client = await acreate_client(cfg.supabase_url, cfg.supabase_service_role_key)
                logger.info("Supabase client initialised")
    return _client
