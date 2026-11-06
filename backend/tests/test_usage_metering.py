"""Unit tests for usage metering when Postgres is unreachable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.usage import DatabaseUnavailableError, check_and_record_quota, get_usage_snapshot


@pytest.mark.asyncio
async def test_check_and_record_quota_skips_when_db_down_in_dev() -> None:
    cfg = SimpleNamespace(is_production=False)
    with (
        patch("app.services.usage.get_db_pool", AsyncMock(side_effect=DatabaseUnavailableError("down"))),
        patch("app.config.get_settings", return_value=cfg),
    ):
        await check_and_record_quota("user-1", "stt", 5)


@pytest.mark.asyncio
async def test_check_and_record_quota_raises_when_db_down_in_prod() -> None:
    cfg = SimpleNamespace(is_production=True)
    with (
        patch("app.services.usage.get_db_pool", AsyncMock(side_effect=DatabaseUnavailableError("down"))),
        patch("app.config.get_settings", return_value=cfg),
        pytest.raises(DatabaseUnavailableError),
    ):
        await check_and_record_quota("user-1", "stt", 5)


@pytest.mark.asyncio
async def test_get_usage_snapshot_falls_back_to_postgrest() -> None:
    """When asyncpg cannot connect, read usage via the Supabase REST client."""

    def _chain(data: object) -> MagicMock:
        node = MagicMock()
        node.execute = AsyncMock(return_value=SimpleNamespace(data=data))
        node.eq.return_value = node
        node.limit.return_value = node
        node.select.return_value = node
        return node

    def _table(name: str) -> MagicMock:
        if name == "users":
            return _chain([{"tier": "pro"}])
        if name == "tier_limits":
            return _chain(
                [
                    {
                        "dubbing_seconds": 54000,
                        "tts_chars": 2000000,
                        "stt_seconds": 54000,
                        "translation_chars": 2000000,
                        "voice_clones": 20,
                    }
                ]
            )
        return _chain(
            [
                {
                    "dubbing_seconds": 120,
                    "tts_chars": 10,
                    "stt_seconds": 5,
                    "translation_chars": 8,
                    "voice_clones": 1,
                }
            ]
        )

    sb = MagicMock()
    sb.table.side_effect = _table

    with (
        patch(
            "app.services.usage.get_db_pool",
            AsyncMock(side_effect=DatabaseUnavailableError("down")),
        ),
        patch("app.services.supabase_client.get_supabase", AsyncMock(return_value=sb)),
    ):
        snap = await get_usage_snapshot("user-1")

    assert snap["dubbing_seconds_used"] == 120
    assert snap["dubbing_seconds_limit"] == 54000
    assert snap["voice_clones_used"] == 1
    assert snap["voice_clones_limit"] == 20
