"""Shared store for desktop SSO handoff (browser → app without localhost).

Uses the same Postgres pool as usage metering so all Railway workers see the
same rows. PostgREST + ``desktop_handoffs`` RLS cannot be used here: exchanging
Google PKCE on a Supabase client attaches the user JWT, and this table has no
INSERT policy (service role is supposed to bypass RLS, but a polluted client
does not).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from app.services.usage import get_db_pool

logger = structlog.get_logger(__name__)

_TTL = timedelta(minutes=10)


async def put_handoff(session_id: str, api_key: str) -> None:
    """Store api_key for session_id (overwrites existing)."""
    pool = await get_db_pool()
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


async def take_handoff(session_id: str) -> str | None:
    """Return api_key once and delete the row. Expired rows are ignored."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM desktop_handoffs
            WHERE session_id = $1
            RETURNING api_key, created_at
            """,
            session_id,
        )
    if row is None or not row["api_key"]:
        return None
    created = row["created_at"]
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > _TTL:
            return None
    return str(row["api_key"])
