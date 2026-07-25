"""PayPal endpoints + billing.py refactor regression tests.

PayPal credentials are intentionally UNSET in this sandbox, so every endpoint that
needs live PayPal must return 503 'PayPal is not configured'. Pydantic validation
runs before the 503 guard, so bad email -> 422.

Wix regression: refactor extracted `provision_or_update_user` — Wix sync must still
return 401 on wrong secret and 503 on correct secret (Supabase unset).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

PREFIX = "/api/v1"
LIVE_BASE = "http://localhost:8001"  # actual sandbox backend with real .env


def _live_up() -> bool:
    try:
        return requests.get(f"{LIVE_BASE}/health", timeout=3).status_code == 200
    except requests.RequestException:
        return False


# ─── PayPal: GET /paypal/config ──────────────────────────────────────────────
class TestPaypalConfig:
    def test_config_returns_unconfigured_shape(self, client: TestClient) -> None:
        r = client.get(f"{PREFIX}/paypal/config")
        assert r.status_code == 200
        data = r.json()
        assert data["configured"] is False
        assert data["env"] == "live"
        assert data["currency"] == "AUD"
        assert data["client_id"] == ""
        assert "starter_plan_id" in data
        assert "pro_plan_id" in data


# ─── PayPal: POST /paypal/orders ─────────────────────────────────────────────
class TestPaypalOrders:
    def test_orders_returns_503_when_unconfigured(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/orders",
            json={"email": "u@x.com", "tier": "early_adopters"},
        )
        assert r.status_code == 503
        assert "PayPal is not configured" in r.json().get("detail", "")

    def test_orders_invalid_email_returns_422(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/orders",
            json={"email": "not-an-email", "tier": "early_adopters"},
        )
        assert r.status_code == 422


# ─── PayPal: POST /paypal/subscriptions ──────────────────────────────────────
class TestPaypalSubscriptions:
    def test_subscriptions_pro_returns_503_when_unconfigured(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/subscriptions",
            json={"email": "u@x.com", "tier": "pro"},
        )
        assert r.status_code == 503
        assert "PayPal is not configured" in r.json().get("detail", "")

    def test_subscriptions_free_tier_still_503_when_unconfigured(self, client: TestClient) -> None:
        # Config guard runs before tier validation — expect 503 (would be 400 if configured).
        r = client.post(
            f"{PREFIX}/paypal/subscriptions",
            json={"email": "u@x.com", "tier": "free"},
        )
        assert r.status_code == 503

    def test_subscriptions_invalid_email_returns_422(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/subscriptions",
            json={"email": "bad", "tier": "pro"},
        )
        assert r.status_code == 422


# ─── PayPal: POST /paypal/admin/setup-plans ──────────────────────────────────
class TestPaypalAdminSetupPlans:
    def test_setup_plans_with_correct_admin_returns_503_when_unconfigured(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/admin/setup-plans",
            headers={"X-Admin-Secret": "test-secret-123"},
        )
        assert r.status_code == 503

    def test_setup_plans_without_admin_returns_401(self, client: TestClient) -> None:
        # Admin guard runs before config check (defense-in-depth): 401 for no secret.
        r = client.post(f"{PREFIX}/paypal/admin/setup-plans")
        assert r.status_code == 401

    def test_setup_plans_wrong_admin_returns_401(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/admin/setup-plans",
            headers={"X-Admin-Secret": "wrong"},
        )
        assert r.status_code == 401


# ─── PayPal: POST /paypal/webhook ────────────────────────────────────────────
class TestPaypalWebhook:
    def test_webhook_returns_503_when_unconfigured(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/webhook",
            json={"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}},
        )
        assert r.status_code == 503


# ─── PayPal: webhook provisioning (success paths) ────────────────────────────
# Regression for the structlog reserved-key bug: logger.info(..., event=...) raised
# TypeError ("multiple values for argument 'event'") AFTER provisioning, 500-ing the
# webhook so PayPal retried indefinitely. These exercise the configured success path.
class TestPaypalWebhookProvisioning:
    def _post(self, client: TestClient, event: str, resource: dict, verified: bool = True, claimed: bool = True):
        with (
            patch("app.services.paypal_client.paypal_configured", return_value=True),
            patch("app.services.paypal_client.verify_webhook_signature", new=AsyncMock(return_value=verified)),
            patch("app.routers.paypal._claim_webhook_event", new=AsyncMock(return_value=claimed)),
            patch("app.routers.paypal._release_webhook_event", new=AsyncMock()),
            patch("app.routers.paypal.provision_or_update_user", new=AsyncMock()) as prov,
        ):
            r = client.post(
                f"{PREFIX}/paypal/webhook",
                json={"id": "WH-EVT-1", "event_type": event, "resource": resource},
            )
        return r, prov

    def test_subscription_activated_provisions_tier(self, client: TestClient) -> None:
        r, prov = self._post(client, "BILLING.SUBSCRIPTION.ACTIVATED", {"custom_id": "buyer@example.com|pro"})
        assert r.status_code == 200
        prov.assert_awaited_once_with("buyer@example.com", "pro", "active")

    def test_one_time_capture_provisions_tier(self, client: TestClient) -> None:
        r, prov = self._post(
            client,
            "PAYMENT.CAPTURE.COMPLETED",
            {"purchase_units": [{"custom_id": "b2@example.com|early_adopters"}]},
        )
        assert r.status_code == 200
        prov.assert_awaited_once_with("b2@example.com", "early_adopters", "active")

    def test_subscription_cancelled_downgrades_to_free(self, client: TestClient) -> None:
        r, prov = self._post(client, "BILLING.SUBSCRIPTION.CANCELLED", {"custom_id": "buyer@example.com|pro"})
        assert r.status_code == 200
        prov.assert_awaited_once_with("buyer@example.com", "free", "canceled")

    def test_bad_signature_returns_400_no_provision(self, client: TestClient) -> None:
        r, prov = self._post(
            client,
            "BILLING.SUBSCRIPTION.ACTIVATED",
            {"custom_id": "x@y.com|pro"},
            verified=False,
        )
        assert r.status_code == 400
        prov.assert_not_awaited()

    def test_duplicate_event_is_ignored(self, client: TestClient) -> None:
        # claimed=False simulates a retry of an already-processed event id.
        r, prov = self._post(
            client,
            "BILLING.SUBSCRIPTION.ACTIVATED",
            {"custom_id": "buyer@example.com|pro"},
            claimed=False,
        )
        assert r.status_code == 200
        assert r.json().get("duplicate") is True
        prov.assert_not_awaited()


# ─── Regression: Wix billing sync (billing.py refactor) ──────────────────────
class TestWixSyncRegression:
    def test_wix_sync_wrong_secret_returns_401(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/billing/wix/sync",
            headers={"X-Wix-Sync-Secret": "wrong-secret"},
            json={"email": "u@x.com", "plan_name": "Pro"},
        )
        assert r.status_code == 401

    def test_wix_sync_missing_secret_returns_401(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/billing/wix/sync",
            json={"email": "u@x.com", "plan_name": "Pro"},
        )
        assert r.status_code == 401

    def test_wix_sync_correct_secret_reaches_db_returns_503(self) -> None:
        # This test must run against the LIVE sandbox backend where SUPABASE_URL is empty.
        # TestClient uses conftest-injected fake Supabase URL which causes DNS 500 instead.
        if not _live_up():
            pytest.skip("live backend on :8001 not reachable")
        r = requests.post(
            f"{LIVE_BASE}{PREFIX}/billing/wix/sync",
            headers={"X-Wix-Sync-Secret": "test-secret-123"},
            json={"email": "u@x.com", "plan_name": "Pro"},
            timeout=10,
        )
        # Passed secret guard, hit Supabase which is not configured -> 503 (not 401).
        assert r.status_code == 503, f"expected 503 got {r.status_code}: {r.text}"


# ─── Regression: Stripe hidden ───────────────────────────────────────────────
class TestBillingPlansRegression:
    def test_plans_returns_503(self, client: TestClient) -> None:
        r = client.get(f"{PREFIX}/billing/plans")
        assert r.status_code == 503


# ─── Regression: health ──────────────────────────────────────────────────────
class TestHealthRegression:
    def test_health_returns_200(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
