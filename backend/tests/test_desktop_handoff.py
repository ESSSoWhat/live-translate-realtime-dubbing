"""Desktop SSO handoff store uses a fresh service-role client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.desktop_handoff import put_handoff, take_handoff


def _table_chain() -> MagicMock:
    chain = MagicMock()
    chain.upsert.return_value = chain
    chain.delete.return_value = chain
    chain.eq.return_value = chain
    chain.lt.return_value = chain
    chain.select.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(data=[]))
    return chain


@pytest.mark.asyncio
async def test_put_handoff_does_not_use_shared_supabase_client() -> None:
    """Handoff writes must not reuse a client that may hold a user JWT."""
    chain = _table_chain()
    fresh = MagicMock()
    fresh.table.return_value = chain
    shared = MagicMock()
    with (
        patch("app.services.desktop_handoff.create_auth_client", AsyncMock(return_value=fresh)),
        patch("app.services.supabase_client.get_supabase", AsyncMock(return_value=shared)),
    ):
        await put_handoff("session-id", "lt_key")
    fresh.table.assert_called_with("desktop_handoffs")
    shared.table.assert_not_called()
    chain.upsert.assert_called_once()


@pytest.mark.asyncio
async def test_take_handoff_uses_fresh_client() -> None:
    """Handoff reads must also bypass a polluted shared session."""
    chain = _table_chain()
    fresh = MagicMock()
    fresh.table.return_value = chain
    with patch("app.services.desktop_handoff.create_auth_client", AsyncMock(return_value=fresh)):
        result = await take_handoff("session-id")
    assert result is None
    fresh.table.assert_called_with("desktop_handoffs")
