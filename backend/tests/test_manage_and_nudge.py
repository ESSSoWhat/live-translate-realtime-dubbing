"""Tests for Manage-Plan (PayPal subscription cancel) and nudge A/B analytics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.dependencies import get_current_user

PREFIX = "/api/v1"


def _mock_sb() -> MagicMock:
    """Async-Supabase-like mock supporting insert/select/update/eq → execute()."""
    sb = MagicMock()
    chain = MagicMock()
    chain.insert = MagicMock(return_value=MagicMock(execute=AsyncMock(return_value=MagicMock(data=[{"id": 1}]))))
    chain.select = MagicMock(return_value=chain)
    chain.update = MagicMock(return_value=chain)
    chain.eq = MagicMock(return_value=chain)
    chain.execute = AsyncMock(return_value=MagicMock(data=[]))
    sb.table = MagicMock(return_value=chain)
    return sb


# ─── Manage Plan: GET /paypal/subscription ───────────────────────────────────
class TestGetSubscription:
    def test_returns_plan_snapshot(self, client: TestClient) -> None:
        user = {"id": "u1", "email": "a@b.com", "tier": "pro", "subscription_status": "active",
                "subscription_id": "I-123"}
        client.app.dependency_overrides[get_current_user] = lambda: user
        try:
            # PayPal unconfigured in tests → no live call, paypal_status stays None.
            r = client.get(f"{PREFIX}/paypal/subscription")
        finally:
            client.app.dependency_overrides.clear()
        assert r.status_code == 200
        body = r.json()
        assert body["tier"] == "pro"
        assert body["subscription_id"] == "I-123"
        assert body["subscription_status"] == "active"

    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get(f"{PREFIX}/paypal/subscription").status_code == 401


# ─── Manage Plan: POST /paypal/subscription/cancel ───────────────────────────
class TestCancelSubscription:
    def test_cancel_success(self, client: TestClient) -> None:
        user = {"id": "u1", "email": "a@b.com", "tier": "pro", "subscription_status": "active",
                "subscription_id": "I-123"}
        client.app.dependency_overrides[get_current_user] = lambda: user
        try:
            with (
                patch("app.services.paypal_client.paypal_configured", return_value=True),
                patch("app.routers.paypal.pp.api", new=AsyncMock(return_value={})) as api,
                patch("app.routers.paypal.get_supabase", new=AsyncMock(return_value=_mock_sb())),
            ):
                r = client.post(f"{PREFIX}/paypal/subscription/cancel")
        finally:
            client.app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json() == {"canceled": True, "subscription_id": "I-123"}
        # Cancel call hit the PayPal cancel path.
        assert "/cancel" in api.await_args.args[1]

    def test_cancel_without_subscription_returns_404(self, client: TestClient) -> None:
        user = {"id": "u1", "email": "a@b.com", "tier": "free", "subscription_status": "active",
                "subscription_id": None}
        client.app.dependency_overrides[get_current_user] = lambda: user
        try:
            with patch("app.services.paypal_client.paypal_configured", return_value=True):
                r = client.post(f"{PREFIX}/paypal/subscription/cancel")
        finally:
            client.app.dependency_overrides.clear()
        assert r.status_code == 404

    def test_cancel_requires_auth(self, client: TestClient) -> None:
        assert client.post(f"{PREFIX}/paypal/subscription/cancel").status_code == 401


# ─── Manage Plan: POST /paypal/subscription/revise (switch plan) ─────────────
class TestReviseSubscription:
    def test_revise_returns_approve_url(self, client: TestClient) -> None:
        user = {"id": "u1", "email": "a@b.com", "tier": "starter", "subscription_status": "active",
                "subscription_id": "I-123"}
        client.app.dependency_overrides[get_current_user] = lambda: user
        revise_result = {"status": "ACTIVE", "links": [{"rel": "approve", "href": "https://paypal/approve/x"}]}
        try:
            with (
                patch("app.services.paypal_client.paypal_configured", return_value=True),
                patch("app.routers.paypal._plan_id_for_tier", return_value="P-PRO"),
                patch("app.routers.paypal.pp.api", new=AsyncMock(return_value=revise_result)) as api,
            ):
                r = client.post(f"{PREFIX}/paypal/subscription/revise", json={"tier": "pro"})
        finally:
            client.app.dependency_overrides.clear()
        assert r.status_code == 200
        body = r.json()
        assert body["approve_url"] == "https://paypal/approve/x"
        assert body["tier"] == "pro"
        assert "/revise" in api.await_args.args[1]

    def test_revise_without_subscription_returns_404(self, client: TestClient) -> None:
        user = {"id": "u1", "email": "a@b.com", "tier": "free", "subscription_status": "active",
                "subscription_id": None}
        client.app.dependency_overrides[get_current_user] = lambda: user
        try:
            with patch("app.services.paypal_client.paypal_configured", return_value=True):
                r = client.post(f"{PREFIX}/paypal/subscription/revise", json={"tier": "pro"})
        finally:
            client.app.dependency_overrides.clear()
        assert r.status_code == 404

    def test_revise_invalid_tier_returns_400(self, client: TestClient) -> None:
        user = {"id": "u1", "email": "a@b.com", "tier": "starter", "subscription_id": "I-123"}
        client.app.dependency_overrides[get_current_user] = lambda: user
        try:
            with patch("app.services.paypal_client.paypal_configured", return_value=True):
                r = client.post(f"{PREFIX}/paypal/subscription/revise", json={"tier": "early_adopters"})
        finally:
            client.app.dependency_overrides.clear()
        assert r.status_code == 400

    def test_revise_requires_auth(self, client: TestClient) -> None:
        assert client.post(f"{PREFIX}/paypal/subscription/revise", json={"tier": "pro"}).status_code == 401


# ─── Nudge A/B: POST /analytics/nudge ────────────────────────────────────────
class TestNudgeEvents:
    def test_record_valid_event(self, client: TestClient) -> None:
        with patch("app.routers.analytics.get_supabase", new=AsyncMock(return_value=_mock_sb())):
            r = client.post(
                f"{PREFIX}/analytics/nudge",
                json={"variant": "a", "action": "shown", "feature": "minutes", "surface": "mobile"},
            )
        assert r.status_code == 201
        assert r.json() == {"recorded": True}

    def test_invalid_variant_returns_422(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/analytics/nudge", json={"variant": "c", "action": "shown"})
        assert r.status_code == 422

    def test_invalid_action_returns_422(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/analytics/nudge", json={"variant": "a", "action": "hovered"})
        assert r.status_code == 422


# ─── Nudge A/B: GET /analytics/nudge/stats ───────────────────────────────────
class TestNudgeStats:
    def test_stats_computes_conversion(self, client: TestClient) -> None:
        rows = [
            {"variant": "a", "action": "shown"},
            {"variant": "a", "action": "shown"},
            {"variant": "a", "action": "clicked"},
            {"variant": "b", "action": "shown"},
        ]
        sb = _mock_sb()
        sb.table.return_value.execute = AsyncMock(return_value=MagicMock(data=rows))
        with patch("app.routers.analytics.get_supabase", new=AsyncMock(return_value=sb)):
            r = client.get(f"{PREFIX}/analytics/nudge/stats", headers={"X-Admin-Secret": "test-secret-123"})
        assert r.status_code == 200
        v = r.json()["variants"]
        assert v["a"] == {"shown": 2, "clicked": 1, "conversion": 0.5}
        assert v["b"] == {"shown": 1, "clicked": 0, "conversion": 0.0}

    def test_stats_wrong_admin_returns_401(self, client: TestClient) -> None:
        assert client.get(f"{PREFIX}/analytics/nudge/stats", headers={"X-Admin-Secret": "nope"}).status_code == 401
