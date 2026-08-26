"""Shared store for desktop SSO handoff (browser → app without localhost).

Uses the same Postgres pool as usage metering so all Railway workers see the
same rows. PostgREST + ``desktop_handoffs`` RLS cannot be used here: exchanging
Google PKCE on a Supabase client attaches the user JWT, and this table has no
INSERT policy (service role is supposed to bypass RLS, but a polluted client
does not).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import structlog

from app.services.usage import get_db_pool

logger = structlog.get_logger(__name__)

_TTL = timedelta(minutes=10)
_DB_RETRY_SECONDS = 30.0

_memory: dict[str, tuple[str, datetime]] = {}
_memory_lock = asyncio.Lock()
_skip_db_until = 0.0


async def _memory_put(session_id: str, api_key: str) -> None:
    now = datetime.now(timezone.utc)
    async with _memory_lock:
        _memory[session_id] = (api_key, now)
        expired = [key for key, (_, created) in _memory.items() if now - created > _TTL]
        for key in expired:
            del _memory[key]


async def _memory_take(session_id: str) -> str | None:
    async with _memory_lock:
        item = _memory.pop(session_id, None)
    if item is None:
        return None
    api_key, created = item
    if datetime.now(timezone.utc) - created > _TTL:
        return None
    return api_key


async def _try_db_pool():
    """Return the Postgres pool, or None after a recent connection failure."""
    global _skip_db_until
    if time.monotonic() < _skip_db_until:
        return None
    try:
        return await get_db_pool()
    except Exception as exc:
        _skip_db_until = time.monotonic() + _DB_RETRY_SECONDS
        logger.warning("Desktop handoff Postgres unavailable", error=str(exc))
        return None


async def put_handoff(session_id: str, api_key: str) -> None:
    """Store api_key for session_id (overwrites existing)."""
    await _memory_put(session_id, api_key)
    pool = await _try_db_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO desktop_handoffs (session_id, api_key, created_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (session_id) DO UPDATE
                  SET api_key = EXCLUDED.api_key, created_at = NOW()
                """,
                session_id,
                api_key,
            )
            await conn.execute(
                """
                DELETE FROM desktop_handoffs
                WHERE created_at < NOW() - INTERVAL '10 minutes'
                """
            )
    except Exception as exc:
        logger.warning("Desktop handoff Postgres write failed", error=str(exc))


async def take_handoff(session_id: str) -> str | None:
    """Return api_key once and delete the row. Expired rows are ignored."""
    db_key: str | None = None
    pool = await _try_db_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    DELETE FROM desktop_handoffs
                    WHERE session_id = $1
                    RETURNING api_key, created_at
                    """,
                    session_id,
                )
        except Exception as exc:
            logger.warning("Desktop handoff Postgres read failed", error=str(exc))
            row = None
        if row is not None and row["api_key"]:
            created = row["created_at"]
            expired = False
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                expired = datetime.now(timezone.utc) - created > _TTL
            if not expired:
                db_key = str(row["api_key"])
    mem_key = await _memory_take(session_id)
    return db_key or mem_key
