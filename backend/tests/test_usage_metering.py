"""Unit tests for usage metering when Postgres is unreachable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.usage import DatabaseUnavailableError, check_and_record_quota


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
