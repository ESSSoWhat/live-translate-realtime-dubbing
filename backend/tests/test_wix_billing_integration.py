"""
Integration tests for Wix billing sync, API key provisioning, and Stripe hidden path.

Runs against the deployed backend using the external base URL. Since Supabase is
intentionally NOT configured in this sandbox, endpoints that reach the DB are
expected to return 503 'Supabase not configured'. That is a PASS condition — it
proves the auth guard ran BEFORE the DB call.

Test coverage:
- Health / root
- Wix sync auth guards (missing / wrong / correct secret via X-Wix-Sync-Secret and Bearer)
- Wix sync request validation (Pydantic 422 on bad email)
- Auth API-key endpoint guard
- Stripe hidden endpoints (503 not 500)
- Protected endpoint requires auth
"""

from __future__ import annotations

import os

import pytest
import requests

# External base URL (routes /api -> backend:8001). Non-/api routes go to frontend.
EXTERNAL_BASE = "https://680d7147-b5a2-4285-a64c-e7132959f99d.preview.emergentagent.com"
# Local backend URL — used for endpoints NOT prefixed with /api (e.g. /health, /)
# because the ingress only forwards /api/* to backend:8001.
LOCAL_BASE = "http://localhost:8001"

WIX_SECRET = "test-secret-123"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def api() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Health / root  (unprefixed → hit local backend directly)
# ─────────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, api: requests.Session) -> None:
        r = api.get(f"{LOCAL_BASE}/health", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok"}

    def test_root_returns_json(self, api: requests.Session) -> None:
        r = api.get(f"{LOCAL_BASE}/", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, dict)
        # Just verify it's some JSON message payload
        assert len(body) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Wix sync auth guards
# ─────────────────────────────────────────────────────────────────────────────

class TestWixSyncAuth:
    """POST /api/v1/billing/wix/sync auth guard."""

    ENDPOINT = "/api/v1/billing/wix/sync"

    def test_missing_auth_returns_401(self, api: requests.Session) -> None:
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            json={"email": "user@example.com", "plan_name": "Monthly Language Unlocked - Pro Tier"},
            timeout=10,
        )
        assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"
        assert "Missing Wix sync auth" in r.text or "detail" in r.json()

    def test_wrong_secret_returns_401(self, api: requests.Session) -> None:
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            headers={"X-Wix-Sync-Secret": "wrong"},
            json={"email": "user@example.com", "plan_name": "Monthly Language Unlocked - Pro Tier"},
            timeout=10,
        )
        assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "Invalid Wix sync secret" in detail, f"Unexpected detail: {detail}"

    def test_correct_secret_header_reaches_db_layer(self, api: requests.Session) -> None:
        """With correct X-Wix-Sync-Secret, auth passes → 503 Supabase-not-configured
        (because DB is intentionally unset here)."""
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            headers={"X-Wix-Sync-Secret": WIX_SECRET},
            json={"email": "user@example.com", "plan_name": "Monthly Language Unlocked - Pro Tier"},
            timeout=15,
        )
        assert r.status_code == 503, f"Expected 503 (past auth), got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "Supabase" in detail and "not configured" in detail, f"Unexpected detail: {detail}"

    def test_correct_secret_bearer_reaches_db_layer(self, api: requests.Session) -> None:
        """Bearer form of the same secret must also pass auth."""
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            headers={"Authorization": f"Bearer {WIX_SECRET}"},
            json={"email": "user@example.com", "plan_name": "Monthly Language Unlocked - Pro Tier"},
            timeout=15,
        )
        assert r.status_code == 503, f"Expected 503 (past auth), got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "Supabase" in detail and "not configured" in detail, f"Unexpected detail: {detail}"

    def test_invalid_email_returns_422(self, api: requests.Session) -> None:
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            headers={"X-Wix-Sync-Secret": WIX_SECRET},
            json={"email": "not-an-email"},
            timeout=10,
        )
        assert r.status_code == 422, f"Expected 422 validation error, got {r.status_code}: {r.text}"
        body = r.json()
        assert "detail" in body


# ─────────────────────────────────────────────────────────────────────────────
# Auth /api-key endpoint (Wix-secret guarded)
# ─────────────────────────────────────────────────────────────────────────────

class TestApiKeyAuth:
    ENDPOINT = "/api/v1/auth/api-key"

    def test_wrong_secret_returns_401(self, api: requests.Session) -> None:
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            headers={"X-Wix-Sync-Secret": "wrong"},
            json={"email": "user@example.com"},
            timeout=10,
        )
        assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "Invalid Wix secret" in detail, f"Unexpected detail: {detail}"

    def test_correct_secret_reaches_db_layer(self, api: requests.Session) -> None:
        r = api.post(
            f"{EXTERNAL_BASE}{self.ENDPOINT}",
            headers={"X-Wix-Sync-Secret": WIX_SECRET},
            json={"email": "user@example.com"},
            timeout=15,
        )
        assert r.status_code == 503, f"Expected 503 (past auth), got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "Supabase" in detail and "not configured" in detail, f"Unexpected detail: {detail}"


# ─────────────────────────────────────────────────────────────────────────────
# Stripe hidden
# ─────────────────────────────────────────────────────────────────────────────

class TestStripeHidden:
    def test_list_plans_returns_503_stripe_disabled(self, api: requests.Session) -> None:
        r = api.get(f"{EXTERNAL_BASE}/api/v1/billing/plans", timeout=10)
        assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "Stripe billing is disabled" in detail
        assert "Wix" in detail

    def test_checkout_returns_503_not_500(self, api: requests.Session) -> None:
        """/billing/checkout with any Bearer must not 500 — should reach auth or return 503."""
        r = api.post(
            f"{EXTERNAL_BASE}/api/v1/billing/checkout",
            headers={"Authorization": "Bearer some-fake-jwt-token"},
            json={
                "price_id": "price_xxx",
                "success_url": "https://example.com/ok",
                "cancel_url": "https://example.com/cancel",
            },
            timeout=15,
        )
        # Should be 503 (billing not configured) OR 401/403 (auth failed first) — but NEVER 500.
        assert r.status_code != 500, f"Endpoint returned 500: {r.text}"
        assert r.status_code in (401, 403, 503), f"Unexpected status {r.status_code}: {r.text}"


# ─────────────────────────────────────────────────────────────────────────────
# Protected endpoint auth
# ─────────────────────────────────────────────────────────────────────────────

class TestProtectedEndpoint:
    def test_user_me_requires_auth(self, api: requests.Session) -> None:
        r = api.get(f"{EXTERNAL_BASE}/api/v1/user/me", timeout=10)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}: {r.text}"
