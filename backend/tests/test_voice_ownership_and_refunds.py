"""Speech-voice ownership, quota refunds on failed upstream calls, delete ordering."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.dependencies import get_current_user
from app.routers import proxy


@pytest.fixture(autouse=True)
def _clear_owner_cache():
    proxy._voice_owner_cache.clear()
    yield
    proxy._voice_owner_cache.clear()


def _sb(rows: list[dict], calls: list | None = None) -> MagicMock:
    query = MagicMock()
    for name in ("select", "eq", "delete", "maybe_single"):
        getattr(query, name).return_value = query

    async def execute():
        if calls is not None:
            calls.append("db_execute")
        return SimpleNamespace(data=rows)

    query.execute = execute
    sb = MagicMock()
    sb.table.return_value = query
    return sb


def _as_user(client, uid: str = "u1") -> None:
    client.app.dependency_overrides[get_current_user] = lambda: {"id": uid, "tier": "pro"}


def test_stream_refuses_another_users_clone(client) -> None:
    _as_user(client)
    quota = AsyncMock()
    try:
        with (
            patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([{"user_id": "other"}]))),
            patch("app.routers.proxy.check_and_record_quota", quota),
        ):
            r = client.post("/api/v1/proxy/synthesize/stream", json={"text": "hi", "voice_id": "theirs1"})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 403
    quota.assert_not_called()


def test_owner_lookup_is_cached() -> None:
    sb_factory = AsyncMock(return_value=_sb([{"user_id": "u1"}]))
    with patch("app.routers.proxy.get_supabase", sb_factory):
        asyncio.run(proxy._require_voice_access("mine1", "u1"))
        asyncio.run(proxy._require_voice_access("mine1", "u1"))
    assert sb_factory.await_count == 1


def test_unowned_server_voice_is_allowed_and_lookup_failure_fails_closed() -> None:
    with patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([]))):
        asyncio.run(proxy._require_voice_access("premade1", "u1"))
    proxy._voice_owner_cache.clear()
    with (
        patch("app.routers.proxy.get_supabase", AsyncMock(side_effect=RuntimeError("db down"))),
        pytest.raises(HTTPException) as exc,
    ):
        asyncio.run(proxy._require_voice_access("premade1", "u1"))
    assert exc.value.status_code == 503


def test_transcribe_refunds_when_elevenlabs_fails(client) -> None:
    _as_user(client)
    el = MagicMock()
    el.speech_to_text.convert = AsyncMock(side_effect=RuntimeError("status_code: 500"))
    refund = AsyncMock()
    try:
        with (
            patch("app.routers.proxy.check_and_record_quota", AsyncMock()),
            patch("app.routers.proxy.refund_usage", refund),
            patch("app.routers.proxy._elevenlabs", return_value=el),
        ):
            r = client.post("/api/v1/proxy/transcribe", files={"audio": ("a.mp3", b"\x01" * 64000, "audio/mpeg")})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 502
    refund.assert_awaited_once()
    assert refund.await_args.args[:2] == ("u1", "stt")


def test_synthesize_refunds_both_meters_when_elevenlabs_fails(client) -> None:
    _as_user(client)
    el = MagicMock()
    el.text_to_speech.convert = MagicMock(side_effect=RuntimeError("boom"))
    refund = AsyncMock()
    try:
        with (
            patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([]))),
            patch("app.routers.proxy.check_and_record_quota", AsyncMock()),
            patch("app.routers.proxy.refund_usage", refund),
            patch("app.routers.proxy._elevenlabs", return_value=el),
        ):
            r = client.post("/api/v1/proxy/synthesize", json={"text": "hello there", "voice_id": "premade1"})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert sorted(c.args[1] for c in refund.await_args_list) == ["dub", "tts"]


def test_tts_chars_refunded_when_dub_quota_is_exceeded(client) -> None:
    _as_user(client)

    async def quota(user_id, event_type, qty):
        if event_type == "dub":
            raise proxy.QuotaExceededError(event_type="dub", used=10, limit=10, requested=qty)

    refund = AsyncMock()
    try:
        with (
            patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([]))),
            patch("app.routers.proxy.check_and_record_quota", quota),
            patch("app.routers.proxy.refund_usage", refund),
        ):
            r = client.post("/api/v1/proxy/synthesize/stream", json={"text": "hello", "voice_id": "premade1"})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 402
    refund.assert_awaited_once_with("u1", "tts", 5)


def test_stream_upstream_error_is_http_error_and_refunded(client) -> None:
    _as_user(client)
    refund = AsyncMock()
    opener = AsyncMock(side_effect=HTTPException(status_code=502, detail="Synthesis failed"))
    try:
        with (
            patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([]))),
            patch("app.routers.proxy.check_and_record_quota", AsyncMock()),
            patch("app.routers.proxy.refund_usage", refund),
            patch("app.routers.proxy._open_tts_stream", opener),
        ):
            r = client.post("/api/v1/proxy/synthesize/stream", json={"text": "hello", "voice_id": "premade1"})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert sorted(c.args[1] for c in refund.await_args_list) == ["dub", "tts"]


def test_delete_removes_from_elevenlabs_before_ownership_row() -> None:
    calls: list[str] = []
    el = MagicMock()

    async def el_delete(voice_id):
        calls.append("elevenlabs_delete")

    el.voices.delete = el_delete
    with (
        patch("app.routers.proxy._elevenlabs", return_value=el),
        patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([{"voice_id": "mine1"}], calls))),
    ):
        resp = asyncio.run(proxy.delete_voice("mine1", user={"id": "u1"}))
    assert resp.status_code == 204
    # ownership lookup, then ElevenLabs delete, then the row delete
    assert calls == ["db_execute", "elevenlabs_delete", "db_execute"]


def test_delete_keeps_row_when_elevenlabs_delete_fails() -> None:
    calls: list[str] = []
    el = MagicMock()
    el.voices.delete = AsyncMock(side_effect=RuntimeError("status_code: 500"))
    with (
        patch("app.routers.proxy._elevenlabs", return_value=el),
        patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([{"voice_id": "mine1"}], calls))),
        pytest.raises(HTTPException) as exc,
    ):
        asyncio.run(proxy.delete_voice("mine1", user={"id": "u1"}))
    assert exc.value.status_code == 502
    assert calls == ["db_execute"]  # only the ownership lookup, row kept


def test_delete_not_owned_never_calls_elevenlabs() -> None:
    el = MagicMock()
    el.voices.delete = AsyncMock(side_effect=AssertionError("upstream called"))
    with (
        patch("app.routers.proxy._elevenlabs", return_value=el),
        patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_sb([]))),
        pytest.raises(HTTPException) as exc,
    ):
        asyncio.run(proxy.delete_voice("theirs1", user={"id": "u1"}))
    assert exc.value.status_code == 403
