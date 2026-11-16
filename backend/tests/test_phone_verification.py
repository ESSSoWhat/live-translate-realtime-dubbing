"""Caller phone ownership: SMS one-time codes and the verified-number rule (Twilio and DB mocked)."""

from __future__ import annotations

import asyncio
import re
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
