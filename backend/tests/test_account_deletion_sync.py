"""Two-way account deletion sync (Wix member <-> app account) with in-memory fakes.

Synthetic data only: no network, no real Supabase or Wix.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError

from app.main import create_app
from app.services import account_sync

WIX_ID = "11111111-1111-4111-8111-111111111111"
WIX_ID_OTHER = "22222222-2222-4222-8222-222222222222"
SECRET = "synthetic-sync-secret"
HOOK_SECRET = "synthetic-hook-secret"


# ── fakes ─────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, db: "FakeDB", name: str) -> None:
        self.db, self.name = db, name
        self.filters: list[tuple[str, Any]] = []
        self.op = "select"
        self.payload: dict | None = None
        self.max_rows: int | None = None

    def select(self, *_a: str) -> "_Query":
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self.filters.append((col, val))
        return self

    def is_(self, col: str, val: Any) -> "_Query":
        self.filters.append((col, None if val == "null" else val))
        return self

    def limit(self, n: int) -> "_Query":
        self.max_rows = n
        return self

    def insert(self, payload: dict) -> "_Query":
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload: dict) -> "_Query":
        self.op, self.payload = "update", payload
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def _match(self, row: dict) -> bool:
        return all(row.get(c) == v for c, v in self.filters)

    async def execute(self) -> _Result:
        table = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            assert self.payload is not None
            if self.name == "users" and self.payload.get("wix_member_id"):
                assert all(r.get("wix_member_id") != self.payload["wix_member_id"] for r in table)
            row = {"id": f"{self.name}-{len(table) + 1}", **self.payload}
            table.append(row)
            return _Result([dict(row)])
        matched = [r for r in table if self._match(r)]
        if self.op == "select":
            out = [dict(r) for r in matched]
            return _Result(out[: self.max_rows] if self.max_rows else out)
        if self.op == "update":
            assert self.payload is not None
            for r in matched:
                r.update(self.payload)
            return _Result([dict(r) for r in matched])
        for r in matched:
            table.remove(r)
        return _Result([dict(r) for r in matched])


class FakeDB:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {"users": [], "desktop_handoffs": [], "account_deletion_audit": []}
        self.auth_users: set[str] = set()
        self.table = MagicMock(side_effect=lambda name: _Query(self, name))
        self.auth = MagicMock()
        self.auth.admin.delete_user = AsyncMock(side_effect=self._delete_auth)
        self.auth.admin.get_user_by_id = AsyncMock(side_effect=self._get_auth)
        self.fail_auth_delete = False

    async def _delete_auth(self, uid: str) -> None:
        if self.fail_auth_delete:
            raise AuthApiError("boom", 500, "unexpected_failure")
        if uid not in self.auth_users:
            raise AuthApiError("User not found", 404, "user_not_found")
        self.auth_users.discard(uid)

    async def _get_auth(self, uid: str) -> Any:
        if uid not in self.auth_users:
            raise AuthApiError("User not found", 404, "user_not_found")
        return SimpleNamespace(user=SimpleNamespace(id=uid))

    def add_user(self, **row: Any) -> dict:
        row.setdefault("id", f"u{len(self.tables['users']) + 1}")
        row.setdefault("api_key", None)
        row.setdefault("wix_member_id", None)
        self.tables["users"].append(row)
        if row.get("supabase_uid"):
            self.auth_users.add(row["supabase_uid"])
        return row

    @property
    def audit(self) -> list[dict]:
        return self.tables["account_deletion_audit"]


class FakeWix:
    """Wix members keyed by id -> login email. Mimics the three REST calls we use."""

    def __init__(self) -> None:
        self.members: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_status: int | None = None

    async def request(self, method: str, path: str, *, json_body: dict | None = None) -> tuple[int, dict]:
        self.calls.append((method, path))
        if self.fail_status:
            return self.fail_status, {}
        if method == "POST" and path == "/members/v1/members/query":
            wanted = json_body["query"]["filter"]["loginEmail"]
            # The real API can return more than the exact match; the service must re-check.
            hits = [
                {"id": mid, "loginEmail": email}
                for mid, email in self.members.items()
                if wanted.split("@")[0] in email
            ]
            return 200, {"members": hits}
        if method == "DELETE" and path.startswith("/members/v1/members/"):
            mid = path.rsplit("/", 1)[1]
            if mid in self.members:
                del self.members[mid]
                return 200, {}
            return 404, {}
        raise AssertionError(f"unexpected Wix call {method} {path}")


@pytest.fixture
def env():
    account_sync._in_flight.clear()
    db, wix = FakeDB(), FakeWix()
    settings = SimpleNamespace(
        lt_sync_secret=SECRET,
        account_delete_sync_enabled=True,
        account_sync_webhook_secret=HOOK_SECRET,
        account_sync_protected_emails="owner@example.com",
        supabase_url="https://test.supabase.co",
        supabase_service_role_key="x",
        wix_api_key="k",
        wix_account_id="a",
        wix_site_id="s",
    )
    with (
        patch("app.services.account_sync.create_auth_client", AsyncMock(return_value=db)),
        patch("app.services.account_sync._wix_request", side_effect=wix.request),
        patch("app.services.account_sync.get_settings", return_value=settings),
        patch("app.routers.account.get_settings", return_value=settings),
    ):
        yield SimpleNamespace(db=db, wix=wix, settings=settings, client=TestClient(create_app()))


def _wix_hdr() -> dict:
    return {"X-Wix-Sync-Secret": SECRET}


def _hook(uid: str, email: str | None = None, **over: Any) -> dict:
    body = {"type": "DELETE", "table": "users", "schema": "auth", "record": None,
            "old_record": {"id": uid, **({"email": email} if email else {})}}
    body.update(over)
    return body


def _hook_hdr() -> dict:
    return {"Authorization": f"Bearer {HOOK_SECRET}"}


# ── Wix -> app ────────────────────────────────────────────────────────────────


def test_wix_member_deleted_removes_app_account_and_related_rows(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID, api_key="lt_k1")
    env.db.add_user(id="u2", email="b@example.com", supabase_uid="sb-2", wix_member_id=WIX_ID_OTHER, api_key="lt_k2")
    env.db.tables["desktop_handoffs"] += [{"session_id": "s1", "api_key": "lt_k1"}, {"session_id": "s2", "api_key": "lt_k2"}]
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"
    assert [u["id"] for u in env.db.tables["users"]] == ["u2"]
    assert "sb-1" not in env.db.auth_users and "sb-2" in env.db.auth_users
    assert [h["session_id"] for h in env.db.tables["desktop_handoffs"]] == ["s2"]
    assert env.wix.calls == []  # the Wix member is already gone: nothing to call


def test_wix_member_deleted_is_idempotent(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    for _ in range(2):
        r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
        assert r.status_code == 200
    assert [a["outcome"] for a in env.db.audit] == ["deleted", "no_match"]


def test_wix_member_deleted_never_matches_by_email(env) -> None:
    """A user with no stored wix_member_id is never deleted by a Wix event."""
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1")
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
    assert r.json()["status"] == "no_match"
    assert len(env.db.tables["users"]) == 1 and "sb-1" in env.db.auth_users


def test_wix_member_deleted_requires_secret(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    body = {"member_id": WIX_ID}
    assert env.client.post("/api/v1/account/sync/wix-member-deleted", json=body).status_code == 401
    bad = env.client.post("/api/v1/account/sync/wix-member-deleted", json=body, headers={"X-Wix-Sync-Secret": "nope"})
    assert bad.status_code == 401
    assert len(env.db.tables["users"]) == 1


def test_wix_member_deleted_rejects_non_guid(env) -> None:
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": "x" * 36}, headers=_wix_hdr())
    assert r.status_code == 422


def test_wix_member_deleted_auth_failure_keeps_row_for_retry(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    env.db.fail_auth_delete = True
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
    assert r.status_code == 502
    assert len(env.db.tables["users"]) == 1  # the link survives so a retry can finish
    assert env.db.audit[-1]["outcome"] == "error"
    env.db.fail_auth_delete = False
    assert env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr()).json()["status"] == "deleted"


def test_protected_email_is_never_deleted(env) -> None:
    env.db.add_user(id="u1", email="Owner@Example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
    assert r.json()["status"] == "protected"
    assert len(env.db.tables["users"]) == 1 and "sb-1" in env.db.auth_users


def test_disabled_flag_does_nothing(env) -> None:
    env.settings.account_delete_sync_enabled = False
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
    assert r.json() == {"status": "disabled"}
    assert len(env.db.tables["users"]) == 1


# ── Supabase -> Wix ───────────────────────────────────────────────────────────


def test_supabase_user_deleted_deletes_wix_member_by_stored_id(env) -> None:
    env.wix.members[WIX_ID] = "a@example.com"
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID, api_key="lt_k")
    env.db.auth_users.discard("sb-1")  # Supabase already deleted it
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-1", "a@example.com"), headers=_hook_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == {"auth": "not_found", "wix": "deleted", "app": "deleted"}
    assert env.wix.members == {} and env.db.tables["users"] == []


def test_supabase_user_deleted_finds_wix_member_by_exact_email_only(env) -> None:
    env.wix.members[WIX_ID] = "a@example.com"
    env.wix.members[WIX_ID_OTHER] = "aa@example.com"  # returned by the query, but not equal
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1")
    env.db.auth_users.discard("sb-1")
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-1", "A@Example.com"), headers=_hook_hdr())
    assert r.status_code == 200, r.text
    assert env.wix.members == {WIX_ID_OTHER: "aa@example.com"}


def test_supabase_user_deleted_ambiguous_wix_match_deletes_nothing(env) -> None:
    env.wix.members[WIX_ID] = "a@example.com"
    env.wix.members[WIX_ID_OTHER] = "A@example.com"  # same address, different case: ambiguous
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1")
    env.db.auth_users.discard("sb-1")
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-1", "a@example.com"), headers=_hook_hdr())
    assert r.status_code == 409
    assert len(env.wix.members) == 2 and len(env.db.tables["users"]) == 1


def test_supabase_user_deleted_without_any_match_is_a_noop(env) -> None:
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-x", "nobody@example.com"), headers=_hook_hdr())
    assert r.status_code == 200
    assert r.json()["status"] == "no_match"
    assert all(m != "DELETE" for m, _ in env.wix.calls)


def test_supabase_echo_after_app_delete_is_a_noop(env) -> None:
    """Backend deletes everything; the auth.users webhook then fires and finds nothing."""
    env.wix.members[WIX_ID] = "a@example.com"
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    assert env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr()).json()["status"] == "deleted"
    env.wix.members.clear()
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-1", "a@example.com"), headers=_hook_hdr())
    assert r.json()["status"] == "no_match"
    assert env.db.tables["users"] == []


def test_supabase_user_deleted_does_not_take_row_of_another_live_user(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-live", wix_member_id=WIX_ID)
    env.wix.members[WIX_ID] = "a@example.com"
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-gone", "a@example.com"), headers=_hook_hdr())
    # The users row belongs to a live auth user, so it is kept. The Wix member is found by
    # the exact email, which the deleted auth user owned.
    assert len(env.db.tables["users"]) == 1
    assert r.status_code == 200


def test_supabase_hook_auth_and_shape(env) -> None:
    url = "/api/v1/account/sync/supabase-user-deleted"
    assert env.client.post(url, json=_hook("sb-1")).status_code == 401
    assert env.client.post(url, json=_hook("sb-1"), headers={"Authorization": f"Bearer {SECRET}"}).status_code == 401
    assert env.client.post(url, json=_hook("sb-1", type="INSERT"), headers=_hook_hdr()).status_code == 422
    assert env.client.post(url, json=_hook("sb-1", table="identities"), headers=_hook_hdr()).status_code == 422
    assert env.client.post(url, json={**_hook("sb-1"), "old_record": {}}, headers=_hook_hdr()).status_code == 422


def test_supabase_user_deleted_wix_outage_returns_502_and_keeps_row(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    env.db.auth_users.discard("sb-1")
    env.wix.fail_status = 500
    r = env.client.post("/api/v1/account/sync/supabase-user-deleted", json=_hook("sb-1", "a@example.com"), headers=_hook_hdr())
    assert r.status_code == 502
    assert len(env.db.tables["users"]) == 1
    assert env.db.audit[-1]["outcome"] == "error"


# ── app -> Wix (DELETE /user/me) ──────────────────────────────────────────────


@pytest.fixture
def signed_in(env):
    from app.dependencies import get_current_user

    def _as(row: dict) -> None:
        env.client.app.dependency_overrides[get_current_user] = lambda: dict(row)

    yield _as
    env.client.app.dependency_overrides.clear()


def _delete_me(env, email: str):
    return env.client.request("DELETE", "/api/v1/user/me", json={"confirm_email": email})


def test_delete_me_deletes_wix_member_auth_user_and_rows(env, signed_in) -> None:
    env.wix.members[WIX_ID] = "a@example.com"
    row = env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID, api_key="lt_k")
    signed_in(row)
    r = _delete_me(env, "A@example.com")
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == {"wix": "deleted", "auth": "deleted", "app": "deleted"}
    assert env.wix.members == {} and env.db.tables["users"] == [] and env.db.auth_users == set()
    assert env.db.audit[-1]["origin"] == "app"
    assert "a@example.com" not in str(env.db.audit)  # only the hash is stored
    assert env.db.audit[-1]["email_sha256"] == account_sync.email_digest("a@example.com")


def test_delete_me_requires_typed_email(env, signed_in) -> None:
    row = env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1")
    signed_in(row)
    assert _delete_me(env, "other@example.com").status_code == 400
    assert len(env.db.tables["users"]) == 1


def test_delete_me_without_wix_member_still_succeeds(env, signed_in) -> None:
    row = env.db.add_user(id="u1", email="solo@example.com", supabase_uid="sb-1")
    signed_in(row)
    r = _delete_me(env, "solo@example.com")
    assert r.status_code == 200
    assert r.json()["steps"]["wix"] == "not_found"


def test_delete_me_wix_failure_aborts_before_deleting_anything(env, signed_in) -> None:
    env.wix.members[WIX_ID] = "a@example.com"
    row = env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    signed_in(row)
    env.wix.fail_status = 503
    r = _delete_me(env, "a@example.com")
    assert r.status_code == 502
    assert len(env.db.tables["users"]) == 1 and "sb-1" in env.db.auth_users and WIX_ID in env.wix.members


def test_delete_me_with_sync_off_skips_wix(env, signed_in) -> None:
    env.settings.account_delete_sync_enabled = False
    env.wix.members[WIX_ID] = "a@example.com"
    row = env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    signed_in(row)
    r = _delete_me(env, "a@example.com")
    assert r.json()["steps"] == {"wix": "skipped", "auth": "deleted", "app": "deleted"}
    assert WIX_ID in env.wix.members


def test_delete_me_protected_account_is_403(env, signed_in) -> None:
    row = env.db.add_user(id="u1", email="owner@example.com", supabase_uid="sb-1")
    signed_in(row)
    assert _delete_me(env, "owner@example.com").status_code == 403
    assert len(env.db.tables["users"]) == 1


def test_in_flight_guard_stops_concurrent_echo(env) -> None:
    env.db.add_user(id="u1", email="a@example.com", supabase_uid="sb-1", wix_member_id=WIX_ID)
    account_sync._in_flight.add("u1")
    r = env.client.post("/api/v1/account/sync/wix-member-deleted", json={"member_id": WIX_ID}, headers=_wix_hdr())
    assert r.json()["status"] == "in_progress"
    assert len(env.db.tables["users"]) == 1


# ── Linking ───────────────────────────────────────────────────────────────────


def test_link_wix_member_id_rules() -> None:
    asyncio.run(_link_rules())


async def _link_rules() -> None:
    db = FakeDB()
    db.add_user(id="u1", email="a@example.com")
    db.add_user(id="u2", email="b@example.com", wix_member_id=WIX_ID_OTHER)
    assert await account_sync.link_wix_member_id(db, "u1", WIX_ID.upper()) is True
    assert db.tables["users"][0]["wix_member_id"] == WIX_ID  # stored lower-case
    assert await account_sync.link_wix_member_id(db, "u1", WIX_ID_OTHER) is False  # owned by u2
    assert await account_sync.link_wix_member_id(db, "u2", WIX_ID) is False  # would overwrite
    assert await account_sync.link_wix_member_id(db, "u1", "not-a-guid") is False


def test_api_key_endpoint_links_member_id(env) -> None:
    from app.routers import auth as auth_router

    env.db.add_user(id="u1", email="a@example.com", api_key="lt_existing", tier="free")
    with (
        patch.object(auth_router, "get_supabase", AsyncMock(return_value=env.db)),
        patch.object(auth_router, "get_settings", return_value=env.settings),
    ):
        r = env.client.post(
            "/api/v1/auth/api-key",
            json={"email": "a@example.com", "wix_member_id": WIX_ID},
            headers=_wix_hdr(),
        )
    assert r.status_code == 200, r.text
    assert env.db.tables["users"][0]["wix_member_id"] == WIX_ID


def test_migration_and_hook_sql_are_present() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "migrations"
    sql = (root / "004_account_deletion_sync.sql").read_text()
    assert "wix_member_id" in sql and "account_deletion_audit" in sql and "ENABLE ROW LEVEL SECURITY" in sql
    hook = (root / "optional" / "005_auth_users_delete_webhook.sql").read_text()
    assert "AFTER DELETE ON auth.users" in hook and "<WEBHOOK_SECRET>" in hook
