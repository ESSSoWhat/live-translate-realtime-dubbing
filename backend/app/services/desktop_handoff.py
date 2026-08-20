"""Shared store for desktop SSO handoff (browser → app without localhost).

Uses Postgres via Supabase so all Railway uvicorn workers see the same rows.
Local SQLite previously broke with ``--workers 2`` (write on one worker, poll on another).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from app.services.supabase_client import create_auth_client

logger = structlog.get_logger(__name__)

_TTL = timedelta(minutes=10)


async def put_handoff(session_id: str, api_key: str) -> None:
    """Store api_key for session_id (overwrites existing)."""
    # Fresh service-role client: get_supabase() may still hold a user JWT from
    # login/refresh/OAuth on this worker, which fails RLS (no INSERT policy).
    sb = await create_auth_client()
    now = datetime.now(timezone.utc)
    await (
        sb.table("desktop_handoffs")
        .upsert(
            {
                "session_id": session_id,
                "api_key": api_key,
                "created_at": now.isoformat(),
            }
        )
        .execute()
    )
    cutoff = (now - _TTL).isoformat()
    try:
        await sb.table("desktop_handoffs").delete().lt("created_at", cutoff).execute()
    except Exception as exc:
        logger.debug("Handoff TTL purge skipped", error=str(exc))


async def take_handoff(session_id: str) -> str | None:
    """Return api_key once and delete the row. Expired rows are ignored."""
    sb = await create_auth_client()
    result = (
        await sb.table("desktop_handoffs")
        .delete()
        .eq("session_id", session_id)
        .select("api_key", "created_at")
        .execute()
    )
    if not result.data:
        return None
    row = result.data[0] if isinstance(result.data, list) else result.data
    if not row or not row.get("api_key"):
        return None
    created_raw = row.get("created_at")
    if created_raw:
        try:
            created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created > _TTL:
                return None
        except ValueError:
            pass
    return str(row["api_key"])
