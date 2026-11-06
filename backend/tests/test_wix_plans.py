"""Wix tier refresh must not wipe a paid plan when Pricing Plans API fails."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import wix_plans


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def _cfg(*, site_id: str = "site-1") -> SimpleNamespace:
    return SimpleNamespace(
        wix_api_key="wix-key",
        wix_account_id="acct-1",
        wix_site_id=site_id,
    )


@pytest.fixture(autouse=True)
def _clear_wix_cache() -> None:
    wix_plans._refresh_cache.clear()
    wix_plans._cached_site_id = None
    yield
    wix_plans._refresh_cache.clear()
    wix_plans._cached_site_id = None


@pytest.mark.asyncio
async def test_orders_428_keeps_existing_tier() -> None:
    """PRICING_PLANS_NOT_INSTALLED must not map to free/canceled."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=_Resp(200, {"members": [{"id": "mem-1"}]}))
    client.get = AsyncMock(return_value=_Resp(428, {}, "PRICING_PLANS_NOT_INSTALLED"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(wix_plans, "get_settings", return_value=_cfg()),
        patch.object(wix_plans.httpx, "AsyncClient", return_value=client),
    ):
        assert await wix_plans.fetch_wix_tier_for_email("user@test.com") is None


@pytest.mark.asyncio
async def test_member_on_second_site_returns_paid_tier() -> None:
    """Skip sites without the member; use the site that has Pricing Plans + a plan."""

    async def _post(url: str, **kwargs):
        if "site-list" in url:
            return _Resp(
                200,
                {"sites": [{"id": "site-a"}, {"id": "site-b"}]},
            )
        site_id = kwargs.get("headers", {}).get("wix-site-id")
        if site_id == "site-a":
            return _Resp(200, {"members": []})
        return _Resp(200, {"members": [{"id": "mem-b"}]})

    async def _get(url: str, **kwargs):
        return _Resp(
            200,
            {
                "orders": [
                    {
                        "status": "ACTIVE",
                        "planId": "954bf0a7-cb41-4726-ace1-c61c7a75425c",
                        "planName": "Early Adopters Life Time Access",
                    }
                ]
            },
        )

    client = AsyncMock()
    client.post = AsyncMock(side_effect=_post)
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(wix_plans, "get_settings", return_value=_cfg(site_id="")),
        patch.object(wix_plans.httpx, "AsyncClient", return_value=client),
    ):
        assert await wix_plans.fetch_wix_tier_for_email("user@test.com") == (
            "early_adopters",
            "active",
        )


@pytest.mark.asyncio
async def test_no_member_on_any_site_is_free() -> None:
    client = AsyncMock()
    client.post = AsyncMock(return_value=_Resp(200, {"members": []}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(wix_plans, "get_settings", return_value=_cfg()),
        patch.object(wix_plans.httpx, "AsyncClient", return_value=client),
    ):
        assert await wix_plans.fetch_wix_tier_for_email("nobody@test.com") == (
            "free",
            "canceled",
        )


@pytest.mark.asyncio
async def test_apply_does_not_write_when_lookup_fails() -> None:
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()

    with (
        patch.object(wix_plans, "fetch_wix_tier_for_email", AsyncMock(return_value=None)),
        patch.object(wix_plans, "get_supabase", AsyncMock(return_value=sb)),
        patch.object(wix_plans, "get_settings", return_value=_cfg()),
        patch.object(wix_plans, "_wix_configured", return_value=True),
    ):
        assert await wix_plans.apply_wix_tier_to_user("u1", "user@test.com", force=True) is None
    sb.table.assert_not_called()
