"""Desktop SSO handoff store uses the Postgres pool, not PostgREST."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.desktop_handoff import put_handoff, take_handoff


def _pool_with_conn(conn: MagicMock) -> MagicMock:
    ctx = AsyncMock()
    ctx.__aenter__.return_value = conn
    ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = ctx
    return pool


@pytest.mark.asyncio
async def test_put_handoff_writes_via_postgres() -> None:
    """Handoff inserts must not go through PostgREST (RLS + user JWT)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    with patch("app.services.desktop_handoff.get_db_pool", AsyncMock(return_value=_pool_with_conn(conn))):
        await put_handoff("session-id", "lt_key")
    assert conn.execute.await_count == 2
    insert_sql = conn.execute.await_args_list[0].args[0]
    assert "INSERT INTO desktop_handoffs" in insert_sql
    assert conn.execute.await_args_list[0].args[1] == "session-id"
    assert conn.execute.await_args_list[0].args[2] == "lt_key"


@pytest.mark.asyncio
async def test_take_handoff_deletes_via_postgres() -> None:
    """Handoff reads consume the row over Postgres."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "api_key": "lt_key",
            "created_at": datetime.now(timezone.utc) - timedelta(seconds=5),
        }
    )
    with patch("app.services.desktop_handoff.get_db_pool", AsyncMock(return_value=_pool_with_conn(conn))):
        result = await take_handoff("session-id")
    assert result == "lt_key"
    conn.fetchrow.assert_awaited_once()
    assert "DELETE FROM desktop_handoffs" in conn.fetchrow.await_args.args[0]
