"""Translated phone call bridge: real SDK calls, metering, refunds, ordering, TwiML."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import twilio_media_bridge as bridge


@pytest.fixture(autouse=True)
def _clear_sessions():
    bridge._session_meta.clear()
    bridge._sessions.clear()
    yield
    bridge._session_meta.clear()
    bridge._sessions.clear()



def _processor() -> bridge.AudioProcessor:
    return bridge.AudioProcessor(leg="user", session_id="s", target_lang="es", user_id="u1")


def _fake_eleven(text: str = "hola", fail_tts: bool = False) -> MagicMock:
    el = MagicMock()
    el.speech_to_text.convert = AsyncMock(return_value=SimpleNamespace(text=text))

    async def audio():
        yield b"\xff" * 10
        yield b"\x7f" * 5

    def tts(voice_id, **kwargs):
        if fail_tts:
            raise RuntimeError("tts down")
        el.tts_calls.append((voice_id, kwargs))
        return audio()

    el.tts_calls = []
    el.text_to_speech.convert = tts
    el.voices.get_all = AsyncMock(side_effect=AssertionError("voices listed"))
    return el


def test_chunk_is_metered_and_spoken_in_a_shared_voice_as_mulaw() -> None:
    proc = _processor()
    el = _fake_eleven()
    proc._client = el
    quota = AsyncMock()
    sent = []
    with (
        patch.object(bridge, "check_and_record_quota", quota),
        patch.object(bridge, "get_settings", return_value=SimpleNamespace(elevenlabs_api_key="sk_x", openai_api_key="")),
        patch.object(proc, "_translate", AsyncMock(return_value="hello")),
        patch.object(proc, "_send_to_other_leg", AsyncMock(side_effect=sent.append)),
    ):
        asyncio.run(proc._process(b"\xff" * 12000))
    assert sent == [b"\xff" * 10 + b"\x7f" * 5]
    voice_id, kwargs = el.tts_calls[0]
    assert voice_id == bridge.DEFAULT_CALL_VOICE_ID and kwargs["output_format"] == "ulaw_8000"
    charged = [(c.args[1], c.args[2]) for c in quota.await_args_list]
    assert charged == [("stt", 1), ("translate", 4), ("tts", 5)]


def test_stt_seconds_carry_the_fraction() -> None:
    proc = _processor()
    assert [proc._stt_seconds_due(12000) for _ in range(4)] == [1, 2, 1, 2]  # 1.5 s chunks


def test_failed_tts_is_refunded() -> None:
    proc = _processor()
    proc._client = _fake_eleven(fail_tts=True)
    refund = AsyncMock()
    with (
        patch.object(bridge, "check_and_record_quota", AsyncMock()),
        patch.object(bridge, "refund_usage", refund),
        patch.object(bridge, "get_settings", return_value=SimpleNamespace(elevenlabs_api_key="sk_x", openai_api_key="")),
        patch.object(proc, "_translate", AsyncMock(return_value="hello")),
    ):
        asyncio.run(proc._process(b"\xff" * 12000))
    refund.assert_awaited_once_with("u1", "tts", 5)


def test_quota_exhausted_stops_the_leg_before_any_paid_call() -> None:
    proc = _processor()
    el = _fake_eleven()
    proc._client = el

    async def over(user_id, event_type, qty):
        raise bridge.QuotaExceededError(event_type=event_type, used=1, limit=1, requested=qty)

    with (
        patch.object(bridge, "check_and_record_quota", over),
        patch.object(bridge, "get_settings", return_value=SimpleNamespace(elevenlabs_api_key="sk_x", openai_api_key="")),
    ):
        asyncio.run(proc._process(b"\xff" * 12000))
        asyncio.run(proc._process(b"\xff" * 12000))
    el.speech_to_text.convert.assert_not_awaited()
    assert proc._stopped


def test_finished_chunk_tasks_are_not_kept_for_the_whole_call() -> None:
    async def run():
        proc = _processor()
        with patch.object(proc, "_process", AsyncMock()):
            payload = base64.b64encode(b"\xff" * 12000).decode()
            for _ in range(5):
                await proc.add_chunk(payload)
                await asyncio.sleep(0)
        return len(proc._pending)

    assert asyncio.run(run()) <= 1


def test_twiml_rejects_unknown_sessions_and_escapes_the_url(client) -> None:
    assert client.get("/api/v1/voice/twiml/stream", params={"leg": "user", "session": '"/><Hangup/>'}).status_code == 404
    session = "A" * 24
    bridge.register_session(session, "u1", "es")
    try:
        r = client.get("/api/v1/voice/twiml/stream", params={"leg": "dest", "session": session})
        assert client.get("/api/v1/voice/twiml/stream", params={"leg": "other", "session": session}).status_code == 404
    finally:
        bridge.unregister_leg(session, "dest")
    assert r.status_code == 200
    assert "&amp;session=" + session in r.text



def test_missing_elevenlabs_key_does_not_charge_stt() -> None:
    proc = _processor()
    el = _fake_eleven()
    proc._client = el
    quota = AsyncMock()
    with (
        patch.object(bridge, "check_and_record_quota", quota),
        patch.object(bridge, "get_settings", return_value=SimpleNamespace(elevenlabs_api_key="", openai_api_key="")),
        patch.object(proc, "_send_to_other_leg", AsyncMock()),
    ):
        asyncio.run(proc._process(b"\xff" * 12000))
    quota.assert_not_awaited()
    el.speech_to_text.convert.assert_not_awaited()
    assert not proc._stopped


def test_one_disconnected_leg_keeps_the_session_counted() -> None:
    sid = "B" * 24
    bridge.register_session(sid, "u1", "es")
    try:
        bridge.register_leg(sid, "user", object(), "MS1")
        assert bridge.active_session_count("u1") == 1
        bridge.unregister_leg(sid, "user")
        # The dest leg may still be ringing; do not free the slot yet.
        assert bridge.active_session_count("u1") == 1
        assert bridge.get_session_meta(sid) is not None
        # TwiML for the remaining leg must still resolve the session.
        assert bridge.get_session_meta(sid)["stream_tokens"]["dest"]
    finally:
        bridge.drop_session(sid)
    assert bridge.active_session_count("u1") == 0


def test_session_drops_only_after_both_legs_have_connected_and_left() -> None:
    sid = "C" * 24
    bridge.register_session(sid, "u1", "es")
    bridge.register_leg(sid, "user", object(), "MS1")
    bridge.register_leg(sid, "dest", object(), "MS2")
    bridge.unregister_leg(sid, "user")
    assert bridge.get_session_meta(sid) is not None
    bridge.unregister_leg(sid, "dest")
    assert bridge.get_session_meta(sid) is None
    assert bridge.active_session_count("u1") == 0
