"""Caller phone ownership: SMS one-time codes and the verified-number rule (Twilio and DB mocked)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_current_user
from app.main import create_app
from app.routers import voice
from app.services import call_guard, phone_verify
from app.services import twilio_media_bridge as bridge

PHONE = "+61412345678"


def _cfg(**kw) -> Settings:
    base = dict(
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_phone_number="+61290000000",
        live_translate_public_url="https://api.example",
        backend_env="production",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clean():
    phone_verify.reset()
    bridge._session_meta.clear()
    bridge._sessions.clear()
    call_guard.rate_limiter._starts.clear()
    yield
    phone_verify.reset()
    bridge._session_meta.clear()
    bridge._sessions.clear()
    call_guard.rate_limiter._starts.clear()


class FakeTable:
    """Just enough of the supabase query builder for users lookups/updates."""

    def __init__(self, db: dict):
        self.db = db
        self.op = None
        self.filters: list[tuple[str, str, object]] = []
        self.values: dict | None = None

    def select(self, *_):
        self.op = "select"
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def limit(self, _):
        return self

    def _match(self, row):
        return all((row.get(c) == v) if op == "eq" else (row.get(c) != v) for op, c, v in self.filters)

    async def execute(self):
        rows = [r for r in self.db.values() if self._match(r)]
        if self.op == "update":
            phone = self.values.get("phone_e164")
            if phone and any(r.get("phone_e164") == phone and r not in rows for r in self.db.values()):
                raise Exception("23505 duplicate key value violates unique constraint")
            for r in rows:
                r.update(self.values)
        return SimpleNamespace(data=[dict(r) for r in rows])


def _setup(user: dict, db: dict, cfg: Settings):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: db.get(user["id"], user)
    sb = MagicMock()
    sb.table.side_effect = lambda name: FakeTable(db)
    tw = MagicMock()
    tw.messages.create.side_effect = lambda **kw: SimpleNamespace(sid="SM1")
    tw.calls.create.side_effect = lambda **kw: SimpleNamespace(sid="CA" + kw["to"][-4:])
    patches = [
        patch.object(voice, "get_settings", return_value=cfg),
        patch.object(voice, "get_supabase", AsyncMock(return_value=sb)),
        patch.object(voice, "check_quota", AsyncMock()),
        patch("twilio.rest.Client", return_value=tw),
    ]
    for p in patches:
        p.start()
    return TestClient(app), tw, patches


@pytest.fixture
def env():
    db = {
        "u1": {"id": "u1", "tier": "pro", "subscription_status": "active"},
        "u2": {"id": "u2", "tier": "pro", "subscription_status": "active"},
    }
    made = []

    def make(user_id="u1", cfg=None):
        client, tw, patches = _setup(db[user_id], db, cfg or _cfg())
        made.append(patches)
        client.__enter__()
        return client, tw

    yield make, db
    for patches in made:
        for p in patches:
            p.stop()


def _sent_code(tw) -> str:
    return re.search(r"\b(\d{6})\b", tw.messages.create.call_args.kwargs["body"]).group(1)


def test_verify_then_call_rings_the_verified_number(env) -> None:
    make, db = env
    client, tw = make()
    assert client.get("/api/v1/voice/phone").json() == {"phone": None, "verified": False, "verified_at": None}

    r = client.post("/api/v1/voice/phone/verify/start", json={"phone": "+61 412 345 678"})
    assert r.status_code == 200 and r.json()["phone"] == PHONE
    sms = tw.messages.create.call_args.kwargs
    assert sms["to"] == PHONE and sms["from_"] == "+61290000000"
    code = _sent_code(tw)
    assert code not in str(phone_verify._pending)  # only the hash is kept

    r = client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": code})
    assert r.status_code == 200 and r.json()["verified"] is True
    assert db["u1"]["phone_e164"] == PHONE and db["u1"]["phone_verified_at"]
    assert client.get("/api/v1/voice/phone").json()["phone"] == PHONE

    r = client.post("/api/v1/voice/translated-call", json={"dest_phone": "+14155550123"})
    assert r.status_code == 200, r.text
    assert [c.kwargs["to"] for c in tw.calls.create.call_args_list] == [PHONE, "+14155550123"]


def test_call_refused_until_verified_and_other_numbers_rejected(env) -> None:
    make, db = env
    client, tw = make()
    r = client.post("/api/v1/voice/translated-call", json={"user_phone": PHONE, "dest_phone": "+14155550123"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "phone_not_verified"
    db["u1"].update(phone_e164=PHONE, phone_verified_at="2026-11-17T00:00:00+00:00")
    r = client.post("/api/v1/voice/translated-call", json={"user_phone": "+61400000001", "dest_phone": "+14155550123"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "phone_mismatch"
    r = client.post("/api/v1/voice/translated-call", json={"user_phone": "+61 412 345 678", "dest_phone": "+14155550123"})
    assert r.status_code == 200  # matching legacy field is fine
    tw.calls.create.assert_called()


def test_wrong_codes_are_limited_and_codes_expire(env) -> None:
    make, _ = env
    client, tw = make()
    client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE})
    code = _sent_code(tw)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(phone_verify.MAX_ATTEMPTS):
        assert client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": wrong}).status_code == 400
    r = client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": code})
    assert r.status_code == 400 and "Too many" in r.json()["detail"]["message"]

    phone_verify.reset()
    client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE})
    code = _sent_code(tw)
    phone_verify._pending["u1"].expires -= phone_verify.CODE_TTL_SEC + 1
    r = client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": code})
    assert r.status_code == 400 and "expired" in r.json()["detail"]["message"]


def test_code_for_one_number_cannot_verify_another(env) -> None:
    make, db = env
    client, tw = make()
    client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE})
    code = _sent_code(tw)
    r = client.post("/api/v1/voice/phone/verify/confirm", json={"phone": "+61400000001", "code": code})
    assert r.status_code == 400 and "phone_e164" not in db["u1"]


def test_resend_gap_and_hourly_send_limit() -> None:
    cfg = _cfg()
    tw = MagicMock()
    with patch("twilio.rest.Client", return_value=tw):
        asyncio.run(phone_verify.send_code("u1", PHONE, cfg, now=1000.0))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(phone_verify.send_code("u1", PHONE, cfg, now=1010.0))
        assert exc.value.status_code == 429 and int(exc.value.headers["Retry-After"]) == 50
        # Another account can't keep texting the same number either.
        with pytest.raises(HTTPException):
            asyncio.run(phone_verify.send_code("u9", PHONE, cfg, now=1020.0))
        t = 1000.0
        for _ in range(phone_verify.MAX_SENDS_PER_HOUR - 1):
            t += phone_verify.RESEND_GAP_SEC + 1
            asyncio.run(phone_verify.send_code("u1", PHONE, cfg, now=t))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(phone_verify.send_code("u1", PHONE, cfg, now=t + 120))
        assert "Too many" in exc.value.detail["message"]
    assert tw.messages.create.call_count == phone_verify.MAX_SENDS_PER_HOUR


def test_number_belongs_to_one_account(env) -> None:
    make, db = env
    db["u2"].update(phone_e164=PHONE, phone_verified_at="2026-11-17T00:00:00+00:00")
    client, tw = make("u1")
    r = client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "phone_in_use"
    tw.messages.create.assert_not_called()


def test_unique_violation_on_confirm_is_409(env) -> None:
    make, db = env
    client, tw = make("u1")
    client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE})
    code = _sent_code(tw)
    db["u2"].update(phone_e164=PHONE)  # another account won the race
    r = client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": code})
    assert r.status_code == 409


def test_free_plan_and_premium_numbers_get_no_sms(env) -> None:
    make, db = env
    db["u1"]["tier"] = "free"
    client, tw = make()
    assert client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE}).status_code == 403
    db["u1"]["tier"] = "pro"
    r = client.post("/api/v1/voice/phone/verify/start", json={"phone": "+19005551234"})
    assert r.status_code == 422
    tw.messages.create.assert_not_called()


def test_remove_number(env) -> None:
    make, db = env
    db["u1"].update(phone_e164=PHONE, phone_verified_at="2026-11-17T00:00:00+00:00")
    client, _ = make()
    assert client.delete("/api/v1/voice/phone").json() == {"phone": None, "verified": False}
    assert db["u1"]["phone_e164"] is None
    assert client.post("/api/v1/voice/translated-call", json={"dest_phone": "+14155550123"}).status_code == 403


def test_twilio_verify_service_path(env) -> None:
    make, db = env
    cfg = _cfg(twilio_verify_service_sid="VA123")
    client, tw = make(cfg=cfg)
    svc = tw.verify.v2.services.return_value
    svc.verification_checks.create.side_effect = lambda to, code: SimpleNamespace(
        status="approved" if code == "424242" else "pending"
    )
    assert client.post("/api/v1/voice/phone/verify/start", json={"phone": PHONE}).status_code == 200
    tw.verify.v2.services.assert_called_with("VA123")
    svc.verifications.create.assert_called_once_with(to=PHONE, channel="sms")
    tw.messages.create.assert_not_called()
    assert client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": "111111"}).status_code == 400
    r = client.post("/api/v1/voice/phone/verify/confirm", json={"phone": PHONE, "code": "424242"})
    assert r.status_code == 200 and db["u1"]["phone_e164"] == PHONE


# ---------------------------------------------------------------------------
# Supabase store (RPC/table client mocked; no real Twilio or database)
# ---------------------------------------------------------------------------

USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class _Result(SimpleNamespace):
    pass


class _RpcCall:
    def __init__(self, db: "FakeVerifyDb", name: str, params: dict):
        self.db = db
        self.name = name
        self.params = params

    async def execute(self):
        return _Result(data=self.db.call_rpc(self.name, self.params))


class _TableQuery:
    def __init__(self, db: "FakeVerifyDb", table: str):
        self.db = db
        self.table = table
        self.op = "select"
        self.filters: list[tuple[str, str, object]] = []
        self.row: dict | None = None

    def select(self, *_):
        self.op = "select"
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.row, self._on_conflict = "upsert", dict(row), on_conflict
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def _rows(self):
        return self.db.sends if self.table == "phone_verification_sends" else list(self.db.codes.values())

    def _match(self, row: dict) -> bool:
        return all(row.get(c) == v for op, c, v in self.filters if op == "eq")

    async def execute(self):
        if self.op == "upsert":
            assert self.row is not None
            key = self.row["user_id"]
            self.db.codes[key] = dict(self.row)
            return _Result(data=[dict(self.row)])
        if self.op == "delete":
            if self.table == "phone_verification_sends":
                before = len(self.db.sends)
                self.db.sends[:] = [r for r in self.db.sends if not self._match(r)]
                return _Result(data=[{"deleted": before - len(self.db.sends)}])
            removed = [k for k, r in self.db.codes.items() if self._match(r)]
            for k in removed:
                del self.db.codes[k]
            return _Result(data=[{"deleted": len(removed)}])
        return _Result(data=[dict(r) for r in self._rows() if self._match(r)])


class FakeVerifyDb:
    """In-process stand-in for the 003 migration's tables and RPCs."""

    def __init__(self, clock: datetime | None = None):
        self.codes: dict[str, dict] = {}
        self.sends: list[dict] = []
        self.clock = clock or datetime.now(timezone.utc)

    def table(self, name: str) -> _TableQuery:
        return _TableQuery(self, name)

    def rpc(self, name: str, params: dict) -> _RpcCall:
        return _RpcCall(self, name, params)

    def call_rpc(self, name: str, params: dict):
        if name == "try_record_phone_verification_send":
            return self._reserve(params)
        if name == "apply_phone_verification_attempt":
            return self._attempt(params)
        raise KeyError(name)

    def _now(self, params: dict) -> datetime:
        raw = params.get("p_now")
        if raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return self.clock

    def _reserve(self, params: dict) -> dict:
        user_id, phone = params["p_user_id"], params["p_phone"]
        now = self._now(params)
        window_start = now - timedelta(hours=1)
        self.sends[:] = [s for s in self.sends if datetime.fromisoformat(s["sent_at"]) > window_start]
        relevant = [
            s for s in self.sends
            if s["user_id"] == user_id or s["phone_e164"] == phone
        ]
        if relevant:
            last = max(datetime.fromisoformat(s["sent_at"]) for s in relevant)
            if last > now - timedelta(seconds=phone_verify.RESEND_GAP_SEC):
                retry = int(phone_verify.RESEND_GAP_SEC - (now - last).total_seconds())
                return {"status": "resend_gap", "retry_after": max(1, retry)}
        for key, val in (("user_id", user_id), ("phone_e164", phone)):
            group = [s for s in self.sends if s[key] == val]
            if len(group) >= phone_verify.MAX_SENDS_PER_HOUR:
                oldest = min(datetime.fromisoformat(s["sent_at"]) for s in group)
                retry = int(3600 - (now - oldest).total_seconds())
                return {"status": "hourly_limit", "retry_after": max(1, retry)}
        send_id = f"send-{len(self.sends)+1}"
        self.sends.append({"id": send_id, "user_id": user_id, "phone_e164": phone, "sent_at": _iso(now)})
        return {"status": "ok", "send_id": send_id}

    def _attempt(self, params: dict) -> str:
        rec = self.codes.get(params["p_user_id"])
        if rec is None or rec.get("phone_e164") != params["p_phone"]:
            return "missing"
        expires = datetime.fromisoformat(rec["expires_at"])
        if expires <= self.clock:
            self.codes.pop(params["p_user_id"], None)
            return "expired"
        if rec.get("attempts", 0) >= phone_verify.MAX_ATTEMPTS:
            return "too_many"
        approved = params.get("p_approved")
        if approved is True:
            matched = True
        elif approved is False:
            matched = False
        else:
            digest = params.get("p_code_hash")
            matched = bool(digest and rec.get("code_hash") and rec["code_hash"] == digest)
        if matched:
            self.codes.pop(params["p_user_id"], None)
            return "ok"
        rec["attempts"] = rec.get("attempts", 0) + 1
        return "invalid"


def test_supabase_store_rate_limits_attempts_expiry_and_hash_only() -> None:
    db = FakeVerifyDb(clock=datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc))
    store = phone_verify.SupabaseStore(db)
    cfg = _cfg()
    t0 = db.clock
    tw = MagicMock()

    with patch("twilio.rest.Client", return_value=tw):
        asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=t0, store=store))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=t0 + timedelta(seconds=10), store=store))
        assert exc.value.status_code == 429 and int(exc.value.headers["Retry-After"]) == 50
        with pytest.raises(HTTPException):
            asyncio.run(phone_verify.send_code(USER_B, PHONE, cfg, now=t0 + timedelta(seconds=20), store=store))
        t = t0
        for _ in range(phone_verify.MAX_SENDS_PER_HOUR - 1):
            t = t + timedelta(seconds=phone_verify.RESEND_GAP_SEC + 1)
            db.clock = t
            asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=t, store=store))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=t + timedelta(seconds=120), store=store))
        assert "Too many" in exc.value.detail["message"]
    assert tw.messages.create.call_count == phone_verify.MAX_SENDS_PER_HOUR

    # Latest pending row stores only the hash, never the plaintext code.
    code = _sent_code(tw)
    pending = db.codes[USER_A]
    assert pending["phone_e164"] == PHONE
    assert pending["code_hash"] == phone_verify._hash(code, PHONE)
    assert code not in str(db.codes) and code not in pending["code_hash"]

    # Wrong codes increment atomically; the fifth failure still says "isn't right",
    # then the correct code is cancelled.
    db.clock = t
    store_now = phone_verify.SupabaseStore(db)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(phone_verify.MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(phone_verify.check_code(USER_A, PHONE, wrong, cfg, store=store_now))
        assert "isn't right" in exc.value.detail["message"]
    assert db.codes[USER_A]["attempts"] == phone_verify.MAX_ATTEMPTS
    with pytest.raises(HTTPException) as exc:
        asyncio.run(phone_verify.check_code(USER_A, PHONE, code, cfg, store=store_now))
    assert "Too many" in exc.value.detail["message"]

    # A new code can be sent after the hour window (and the resend gap) opens.
    later = t + timedelta(hours=1, seconds=1)
    db.clock = later
    with patch("twilio.rest.Client", return_value=tw):
        asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=later, store=store_now))
    fresh = _sent_code(tw)
    # Expired row is rejected and removed.
    db.codes[USER_A]["expires_at"] = _iso(later - timedelta(seconds=1))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(phone_verify.check_code(USER_A, PHONE, fresh, cfg, store=store_now))
    assert "expired" in exc.value.detail["message"]
    assert USER_A not in db.codes


def test_supabase_store_confirms_valid_code_and_rejects_other_number() -> None:
    db = FakeVerifyDb(clock=datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc))
    store = phone_verify.SupabaseStore(db)
    cfg = _cfg()
    tw = MagicMock()
    with patch("twilio.rest.Client", return_value=tw):
        asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=db.clock, store=store))
    code = _sent_code(tw)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(phone_verify.check_code(USER_A, "+61400000001", code, cfg, store=store))
    assert "Ask for a code" in exc.value.detail["message"]
    assert USER_A in db.codes  # not consumed
    asyncio.run(phone_verify.check_code(USER_A, PHONE, code, cfg, store=store))
    assert USER_A not in db.codes


def test_supabase_store_twilio_verify_path_tracks_attempts() -> None:
    db = FakeVerifyDb(clock=datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc))
    store = phone_verify.SupabaseStore(db)
    cfg = _cfg(twilio_verify_service_sid="VA123")
    tw = MagicMock()
    svc = tw.verify.v2.services.return_value
    svc.verification_checks.create.side_effect = lambda to, code: SimpleNamespace(
        status="approved" if code == "424242" else "pending"
    )
    with patch("twilio.rest.Client", return_value=tw):
        asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=db.clock, store=store))
        tw.verify.v2.services.assert_called_with("VA123")
        svc.verifications.create.assert_called_once_with(to=PHONE, channel="sms")
        tw.messages.create.assert_not_called()
        assert db.codes[USER_A]["code_hash"] is None
        with pytest.raises(HTTPException):
            asyncio.run(phone_verify.check_code(USER_A, PHONE, "111111", cfg, store=store))
        assert db.codes[USER_A]["attempts"] == 1
        asyncio.run(phone_verify.check_code(USER_A, PHONE, "424242", cfg, store=store))
        assert USER_A not in db.codes


def test_supabase_store_rolls_back_send_when_sms_fails() -> None:
    db = FakeVerifyDb(clock=datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc))
    store = phone_verify.SupabaseStore(db)
    cfg = _cfg()
    tw = MagicMock()
    tw.messages.create.side_effect = RuntimeError("twilio down")
    with patch("twilio.rest.Client", return_value=tw), pytest.raises(RuntimeError):
        asyncio.run(phone_verify.send_code(USER_A, PHONE, cfg, now=db.clock, store=store))
    assert db.sends == []
    assert db.codes == {}
