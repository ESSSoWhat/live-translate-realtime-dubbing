"""Translated-call abuse guards: paid plan, numbers, rate limits, Twilio signatures (all mocked)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from twilio.request_validator import RequestValidator

from app.config import Settings
from app.dependencies import get_current_user
from app.main import create_app
from app.routers import voice
from app.services import call_guard
from app.services import twilio_media_bridge as bridge
from fastapi.testclient import TestClient

TOKEN = "test-twilio-token"
PUBLIC = "https://api.livetranslate.example"


def _cfg(**kw) -> Settings:
    base = dict(
        twilio_account_sid="AC123",
        twilio_auth_token=TOKEN,
        twilio_phone_number="+61290000000",
        live_translate_public_url=PUBLIC,
        backend_env="production",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clean_state():
    bridge._session_meta.clear()
    bridge._sessions.clear()
    call_guard.rate_limiter._starts.clear()
    yield
    bridge._session_meta.clear()
    bridge._sessions.clear()
    call_guard.rate_limiter._starts.clear()


def _app_client(user: dict, cfg: Settings):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    twilio_client = MagicMock()
    twilio_client.calls.create.side_effect = lambda **kw: SimpleNamespace(sid="CA" + kw["to"][-4:])
    patches = [
        patch.object(voice, "get_settings", return_value=cfg),
        patch.object(voice, "check_quota", AsyncMock()),
        patch("twilio.rest.Client", return_value=twilio_client),
    ]
    for p in patches:
        p.start()
    return TestClient(app), twilio_client, patches


PAID = {
    "id": "u1",
    "tier": "pro",
    "subscription_status": "active",
    "phone_e164": "+61412345678",
    "phone_verified_at": "2026-11-17T00:00:00+00:00",
}
BODY = {"user_phone": "+61 412 345 678", "dest_phone": "+14155550123", "target_lang": "es"}


def _start(user=PAID, body=BODY, cfg=None):
    client, tw, patches = _app_client(user, cfg or _cfg())
    try:
        with client:
            return client.post("/api/v1/voice/translated-call", json=body), tw
    finally:
        for p in patches:
            p.stop()


def test_free_plan_cannot_start_calls_and_nothing_is_dialled() -> None:
    r, tw = _start(user={"id": "u2", "tier": "free"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "plan_required"
    tw.calls.create.assert_not_called()


def test_past_due_paid_plan_is_refused() -> None:
    r, _ = _start(user={"id": "u3", "tier": "pro", "subscription_status": "past_due"})
    assert r.status_code == 403


def test_paid_user_dials_normalised_numbers_and_signed_twiml_urls() -> None:
    r, tw = _start()
    assert r.status_code == 200, r.text
    calls = [c.kwargs for c in tw.calls.create.call_args_list]
    assert [c["to"] for c in calls] == ["+61412345678", "+14155550123"]
    assert all(c["url"].startswith(PUBLIC + "/api/v1/voice/twiml/stream?") for c in calls)
    assert "token" not in r.json() and "stream_tokens" not in r.text


@pytest.mark.parametrize(
    "number,error",
    [
        ("0412345678", "invalid_phone"),
        ("+61123", "invalid_phone"),  # short code length
        ("12345", "invalid_phone"),
        ("+19005551234", "blocked_destination"),
        ("+61190012345", "blocked_destination"),
        ("+447012345678", "blocked_destination"),
        ("+881612345678", "blocked_destination"),
    ],
)
def test_bad_or_premium_destinations_are_rejected(number: str, error: str) -> None:
    r, tw = _start(body={**BODY, "dest_phone": number})
    assert r.status_code == 422 and r.json()["detail"]["error"] == error
    tw.calls.create.assert_not_called()


def test_allowed_prefixes_and_same_number() -> None:
    r, _ = _start(cfg=_cfg(twilio_allowed_prefixes="+61"))
    assert r.json()["detail"]["error"] == "destination_not_allowed"
    r, _ = _start(body={**BODY, "dest_phone": "+61412345678"})
    assert r.status_code == 422
    r, _ = _start(body={**BODY, "dest_phone": "+61290000000"})  # our own Twilio number
    assert r.status_code == 422


def test_one_call_at_a_time_then_hourly_limit() -> None:
    cfg = _cfg(twilio_max_concurrent_calls=1, twilio_max_calls_per_hour=2)
    assert _start(cfg=cfg)[0].status_code == 200
    r, tw = _start(cfg=cfg)
    assert r.status_code == 429 and r.json()["detail"]["error"] == "call_in_progress"
    tw.calls.create.assert_not_called()
    bridge._session_meta.clear()  # first call ended
    assert _start(cfg=cfg)[0].status_code == 200
    bridge._session_meta.clear()
    r, _ = _start(cfg=cfg)
    assert r.status_code == 429 and r.json()["detail"]["error"] == "call_rate_limited"
    assert int(r.headers["Retry-After"]) > 0


def test_unanswered_calls_stop_counting_as_active() -> None:
    bridge.register_session("S" * 24, "u1", "es")
    assert bridge.active_session_count("u1") == 1
    bridge._session_meta["S" * 24]["created"] -= bridge._UNANSWERED_TTL_SEC + 1
    assert bridge.active_session_count("u1") == 0


def test_production_requires_public_url() -> None:
    r, tw = _start(cfg=_cfg(live_translate_public_url=""))
    assert r.status_code == 503
    tw.calls.create.assert_not_called()


# --- Twilio webhook signatures -------------------------------------------------

SESSION = "A" * 24


def _twiml_client(cfg: Settings) -> tuple[TestClient, object]:
    app = create_app()
    p = patch.object(voice, "get_settings", return_value=cfg)
    p.start()
    bridge.register_session(SESSION, "u1", "es")
    return TestClient(app), p


def _sign(url: str, params: dict | None = None) -> str:
    return RequestValidator(TOKEN).compute_signature(url, params or {})


def test_twiml_post_with_valid_signature_against_public_url() -> None:
    client, p = _twiml_client(_cfg())
    try:
        query = f"leg=user&session={SESSION}"
        form = {"CallSid": "CA1", "From": "+61290000000"}
        sig = _sign(f"{PUBLIC}/api/v1/voice/twiml/stream?{query}", form)
        with client:
            r = client.post(f"/api/v1/voice/twiml/stream?{query}", data=form, headers={"X-Twilio-Signature": sig})
        assert r.status_code == 200, r.text
        token = bridge._session_meta[SESSION]["stream_tokens"]["user"]
        assert f"wss://api.livetranslate.example/api/v1/voice/ws/media?leg=user&amp;session={SESSION}&amp;token={token}" in r.text
    finally:
        p.stop()


def test_twiml_rejects_missing_bad_or_tampered_signatures() -> None:
    client, p = _twiml_client(_cfg())
    try:
        query = f"leg=user&session={SESSION}"
        form = {"CallSid": "CA1"}
        good = _sign(f"{PUBLIC}/api/v1/voice/twiml/stream?{query}", form)
        with client:
            url = f"/api/v1/voice/twiml/stream?{query}"
            assert client.post(url, data=form).status_code == 403
            assert client.post(url, data=form, headers={"X-Twilio-Signature": "bogus"}).status_code == 403
            assert client.post(url, data={"CallSid": "CA2"}, headers={"X-Twilio-Signature": good}).status_code == 403
            # Signed for the internal host instead of the public URL -> rejected.
            internal = _sign(f"http://testserver/api/v1/voice/twiml/stream?{query}", form)
            assert client.post(url, data=form, headers={"X-Twilio-Signature": internal}).status_code == 403
            get_sig = _sign(f"{PUBLIC}{url}")
            assert client.get(url, headers={"X-Twilio-Signature": get_sig}).status_code == 200
    finally:
        p.stop()


def test_production_without_token_fails_closed_dev_allows() -> None:
    req = SimpleNamespace(headers={}, method="GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_guard.verify_twilio_request(req, _cfg(twilio_auth_token="")))
    assert exc.value.status_code == 403
    asyncio.run(call_guard.verify_twilio_request(req, _cfg(twilio_auth_token="", backend_env="development")))


def test_media_websocket_needs_the_leg_token() -> None:
    client, p = _twiml_client(_cfg())
    try:
        token = bridge._session_meta[SESSION]["stream_tokens"]["user"]
        other = bridge._session_meta[SESSION]["stream_tokens"]["dest"]
        with client:
            for bad in ("", "&token=nope", f"&token={other}"):
                with client.websocket_connect(f"/api/v1/voice/ws/media?leg=user&session={SESSION}{bad}") as ws:
                    msg = ws.receive()
                    assert msg["type"] == "websocket.close" and msg["code"] == 4004
            with client.websocket_connect(f"/api/v1/voice/ws/media?leg=user&session={SESSION}&token={token}") as ws:
                ws.send_text('{"event": "stop"}')
    finally:
        p.stop()
