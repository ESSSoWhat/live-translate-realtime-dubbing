"""PayPal endpoints + billing.py refactor regression tests.

PayPal credentials are intentionally UNSET in this sandbox, so every endpoint that
needs live PayPal must return 503 'PayPal is not configured'. Pydantic validation
runs before the 503 guard, so bad email -> 422.

Wix regression: refactor extracted `provision_or_update_user` — Wix sync must still
return 401 on wrong secret and 503 on correct secret (Supabase unset).
"""

from __future__ import annotations

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

    def test_setup_plans_without_admin_returns_503_when_unconfigured(self, client: TestClient) -> None:
        # Config guard runs before admin check — 503, not 401.
        r = client.post(f"{PREFIX}/paypal/admin/setup-plans")
        assert r.status_code == 503

    def test_setup_plans_wrong_admin_returns_503_when_unconfigured(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/admin/setup-plans",
            headers={"X-Admin-Secret": "wrong"},
        )
        assert r.status_code == 503


# ─── PayPal: POST /paypal/webhook ────────────────────────────────────────────
class TestPaypalWebhook:
    def test_webhook_returns_503_when_unconfigured(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/paypal/webhook",
            json={"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}},
        )
        assert r.status_code == 503


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
