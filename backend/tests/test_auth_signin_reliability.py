"""Email/password sign-up and sign-in edge cases with an in-memory Supabase fake.

Synthetic data only: no network, no real Supabase project.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError, AuthRetryableError

from app.main import create_app
from tests.conftest import _usage_snapshot


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """Tiny PostgREST-like builder over a list of dict rows."""

    def __init__(self, table: "FakeUsersTable") -> None:
        self._table = table
        self._filters: list[tuple[str, Any]] = []
        self._op = "select"
        self._payload: dict | None = None

    def select(self, *_cols: str) -> "_Query":
        self._op = "select"
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self._filters.append((column, value))
        return self

    def limit(self, _n: int) -> "_Query":
        return self

    def maybe_single(self) -> "_Query":
        return self

    def insert(self, payload: dict) -> "_Query":
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict) -> "_Query":
        self._op = "update"
        self._payload = payload
        return self

    def _matches(self, row: dict) -> bool:
        return all(row.get(col) == val for col, val in self._filters)

    async def execute(self) -> _Result:
        rows = self._table.rows
        if self._op == "select":
            return _Result([dict(r) for r in rows if self._matches(r)][:1])
        if self._op == "insert":
            assert self._payload is not None
            for unique in ("email", "supabase_uid", "api_key"):
                value = self._payload.get(unique)
                if value is not None and any(r.get(unique) == value for r in rows):
                    raise RuntimeError(f'duplicate key value violates unique constraint "users_{unique}_key"')
            row = {"id": len(rows) + 1, "tier": "free", "api_key": None, **self._payload}
            rows.append(row)
            return _Result([dict(row)])
        assert self._payload is not None
        changed = []
        for r in rows:
            if self._matches(r):
                r.update(self._payload)
                changed.append(dict(r))
        return _Result(changed)


class FakeUsersTable:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: list[dict] = rows or []


def _session(token: str = "at-ok") -> MagicMock:
    s = MagicMock()
    s.access_token = token
    s.refresh_token = "rt-ok"
    s.expires_in = 3600
    return s


def _user(uid: str, email: str) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.email = email
    return u


def _make_sb(table: FakeUsersTable, existing_auth_uids: set[str] | None = None) -> MagicMock:
    sb = MagicMock()
    sb.table = MagicMock(side_effect=lambda _name: _Query(table))
    alive = existing_auth_uids or set()

    async def get_user_by_id(uid: str) -> MagicMock:
        if uid in alive:
            return MagicMock(user=_user(uid, "other@example.com"))
        raise AuthApiError("User not found", 404, "user_not_found")

    sb.auth.admin.get_user_by_id = AsyncMock(side_effect=get_user_by_id)
    sb.auth.admin.delete_user = AsyncMock(return_value=None)
    return sb


@pytest.fixture
def make_client():
    clients: list = []

    def _factory(sb: MagicMock) -> TestClient:
        async def fake_usage(_user_id: str) -> dict:
            return _usage_snapshot()

        patches = [
            patch("app.routers.auth.get_supabase", AsyncMock(return_value=sb)),
            patch("app.routers.auth.create_auth_client", AsyncMock(return_value=sb)),
            patch("app.routers.auth.get_usage_snapshot", side_effect=fake_usage),
        ]
        for p in patches:
            p.start()
            clients.append(p)
        return TestClient(create_app())

    yield _factory
    for p in clients:
        p.stop()


SIGNUP = {"email": "fresh@example.com", "password": "Synthetic-Pass-123"}


# ── Sign-up error mapping ─────────────────────────────────────────────────────


def test_register_smtp_failure_is_503_with_clear_message(make_client) -> None:
    """Supabase 500 'Error sending confirmation email' (bad SMTP login) is not a 400."""
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_up = AsyncMock(
        side_effect=AuthApiError("Error sending confirmation email", 500, "unexpected_failure")
    )
    r = make_client(sb).post("/api/v1/auth/register", json=SIGNUP)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "couldn't send your confirmation email" in detail
    # Clients treat "check your email" + "confirm" as success; this must not match.
    assert "check your email" not in detail.lower()


def test_register_existing_account_is_409(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_up = AsyncMock(
        side_effect=AuthApiError("User already registered", 422, "user_already_exists")
    )
    r = make_client(sb).post("/api/v1/auth/register", json=SIGNUP)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_register_rate_limited_is_429(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_up = AsyncMock(
        side_effect=AuthApiError("email rate limit exceeded", 429, "over_email_send_rate_limit")
    )
    r = make_client(sb).post("/api/v1/auth/register", json=SIGNUP)
    assert r.status_code == 429


def test_register_weak_password_is_400(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_up = AsyncMock(
        side_effect=AuthApiError("Password should be at least 8 characters", 422, "weak_password")
    )
    r = make_client(sb).post("/api/v1/auth/register", json=SIGNUP)
    assert r.status_code == 400
    assert "password" in r.json()["detail"].lower()


def test_register_confirmation_required_still_202(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_up = AsyncMock(
        return_value=MagicMock(user=_user("uid-new", SIGNUP["email"]), session=None)
    )
    r = make_client(sb).post("/api/v1/auth/register", json=SIGNUP)
    assert r.status_code == 202
    assert r.json()["needs_confirmation"] is True


def test_register_with_session_links_wix_row(make_client) -> None:
    """Confirmation off: a Wix-provisioned row for the email is linked, not duplicated."""
    table = FakeUsersTable(
        [{"id": 7, "email": SIGNUP["email"], "supabase_uid": None, "tier": "pro", "api_key": "lt_wix"}]
    )
    sb = _make_sb(table)
    sb.auth.sign_up = AsyncMock(
        return_value=MagicMock(user=_user("uid-new", SIGNUP["email"]), session=_session())
    )
    r = make_client(sb).post("/api/v1/auth/register", json=SIGNUP)
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == "7"
    assert len(table.rows) == 1
    assert table.rows[0]["supabase_uid"] == "uid-new"
    sb.auth.admin.delete_user.assert_not_awaited()


# ── Login ─────────────────────────────────────────────────────────────────────


LOGIN = {"email": "member@example.com", "password": "Synthetic-Pass-123"}


def _login_ok(sb: MagicMock, uid: str = "uid-member", email: str = LOGIN["email"]) -> None:
    sb.auth.sign_in_with_password = AsyncMock(
        return_value=MagicMock(user=_user(uid, email), session=_session("at-member"))
    )


def test_login_existing_row(make_client) -> None:
    table = FakeUsersTable([{"id": 3, "email": LOGIN["email"], "supabase_uid": "uid-member", "tier": "free"}])
    sb = _make_sb(table)
    _login_ok(sb)
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] == "at-member"
    assert r.json()["user_id"] == "3"


def test_login_creates_row_when_missing(make_client) -> None:
    table = FakeUsersTable()
    sb = _make_sb(table)
    _login_ok(sb)
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 200, r.text
    assert table.rows[0]["supabase_uid"] == "uid-member"


def test_login_links_wix_row_without_uid(make_client) -> None:
    """Row made by the Wix /api-key sync (no supabase_uid) used to make login 500."""
    table = FakeUsersTable(
        [{"id": 9, "email": LOGIN["email"], "supabase_uid": None, "tier": "pro", "api_key": "lt_wix"}]
    )
    sb = _make_sb(table)
    _login_ok(sb)
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "pro"
    assert table.rows == [
        {"id": 9, "email": LOGIN["email"], "supabase_uid": "uid-member", "tier": "pro", "api_key": "lt_wix"}
    ]


def test_login_relinks_row_of_deleted_auth_user(make_client) -> None:
    """Deleting a user from Supabase Auth leaves public.users behind; re-sign-up must work."""
    table = FakeUsersTable([{"id": 4, "email": LOGIN["email"], "supabase_uid": "uid-deleted", "tier": "free"}])
    sb = _make_sb(table, existing_auth_uids=set())
    _login_ok(sb, uid="uid-recreated")
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 200, r.text
    assert table.rows[0]["supabase_uid"] == "uid-recreated"
    assert len(table.rows) == 1


def test_login_matches_email_case_insensitively(make_client) -> None:
    table = FakeUsersTable([{"id": 5, "email": "member@example.com", "supabase_uid": None, "tier": "free"}])
    sb = _make_sb(table)
    _login_ok(sb, email="Member@Example.com")
    r = make_client(sb).post("/api/v1/auth/login", json={**LOGIN, "email": "Member@Example.com"})
    assert r.status_code == 200, r.text
    assert table.rows[0]["supabase_uid"] == "uid-member"


def test_login_refuses_to_steal_row_of_live_user(make_client) -> None:
    table = FakeUsersTable([{"id": 6, "email": LOGIN["email"], "supabase_uid": "uid-other", "tier": "pro"}])
    sb = _make_sb(table, existing_auth_uids={"uid-other"})
    _login_ok(sb)
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 409
    assert table.rows[0]["supabase_uid"] == "uid-other"


def test_login_unconfirmed_email_is_403_with_guidance(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_in_with_password = AsyncMock(
        side_effect=AuthApiError("Email not confirmed", 400, "email_not_confirmed")
    )
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 403
    assert "Confirm your email" in r.json()["detail"]


def test_login_wrong_password_is_401(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_in_with_password = AsyncMock(
        side_effect=AuthApiError("Invalid login credentials", 400, "invalid_credentials")
    )
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


def test_login_supabase_outage_is_503(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_in_with_password = AsyncMock(side_effect=AuthRetryableError("Bad gateway", 502))
    r = make_client(sb).post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 503


# ── Desktop SSO email/password (DubTool + desktop browser sign-in) ────────────


def test_desktop_sso_password_links_wix_row_and_returns_key(make_client) -> None:
    table = FakeUsersTable(
        [{"id": 11, "email": LOGIN["email"], "supabase_uid": None, "tier": "free", "api_key": "lt_existing"}]
    )
    sb = _make_sb(table)
    _login_ok(sb)
    with (
        patch("app.services.desktop_handoff.put_handoff", new_callable=AsyncMock) as put,
        patch("app.services.wix_plans.refresh_user_tier_from_wix", new_callable=AsyncMock),
    ):
        r = make_client(sb).post(
            "/api/v1/auth/desktop-sso/complete",
            json={"session_id": "synthetic-session-0123456789", **LOGIN},
        )
    assert r.status_code == 200, r.text
    assert r.json()["api_key"] == "lt_existing"
    assert table.rows[0]["supabase_uid"] == "uid-member"
    put.assert_awaited_once()


def test_desktop_sso_password_unconfirmed_is_403(make_client) -> None:
    sb = _make_sb(FakeUsersTable())
    sb.auth.sign_in_with_password = AsyncMock(
        side_effect=AuthApiError("Email not confirmed", 400, "email_not_confirmed")
    )
    r = make_client(sb).post(
        "/api/v1/auth/desktop-sso/complete",
        json={"session_id": "synthetic-session-0123456789", **LOGIN},
    )
    assert r.status_code == 403
    assert "Confirm your email" in r.json()["detail"]
