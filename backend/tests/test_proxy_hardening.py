"""Proxy hardening: voice listing privacy, voice id validation, upload cap, WAV duration."""

from __future__ import annotations

import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.dependencies import get_current_user
from app.routers import proxy


def _wav_bytes(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def test_wav_duration_read_from_header_without_pydub() -> None:
    data = _wav_bytes(3.5)
    with patch("pydub.AudioSegment.from_file", side_effect=AssertionError("pydub used")):
        assert abs(proxy._audio_duration_seconds(data, "audio/wav", 16000) - 3.5) < 0.01


def test_non_wav_is_not_parsed_as_wav() -> None:
    assert proxy._wav_duration_seconds(b"ID3" + b"\x00" * 100) is None


def _supabase_with_owned(voice_ids: list[str]) -> MagicMock:
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"voice_id": v} for v in voice_ids]))
    sb = MagicMock()
    sb.table.return_value = query
    return sb


def test_list_voices_hides_other_users_clones(client) -> None:
    voices = SimpleNamespace(
        voices=[
            SimpleNamespace(voice_id="premade1", name="Rachel", category="premade"),
            SimpleNamespace(voice_id="mine1", name="My clone", category="cloned"),
            SimpleNamespace(voice_id="theirs1", name="Someone else", category="cloned"),
        ]
    )
    el = MagicMock()
    el.voices.get_all = AsyncMock(return_value=voices)
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "pro"}
    try:
        with (
            patch("app.routers.proxy._elevenlabs", return_value=el),
            patch("app.routers.proxy.get_supabase", AsyncMock(return_value=_supabase_with_owned(["mine1"]))),
        ):
            r = client.get("/api/v1/proxy/voices")
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert sorted(v["voice_id"] for v in r.json()) == ["mine1", "premade1"]


def test_list_voices_fails_closed_when_ownership_lookup_fails(client) -> None:
    voices = SimpleNamespace(
        voices=[
            SimpleNamespace(voice_id="premade1", name="Rachel", category="premade"),
            SimpleNamespace(voice_id="theirs1", name="Someone else", category="cloned"),
        ]
    )
    el = MagicMock()
    el.voices.get_all = AsyncMock(return_value=voices)
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "pro"}
    try:
        with (
            patch("app.routers.proxy._elevenlabs", return_value=el),
            patch("app.routers.proxy.get_supabase", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            r = client.get("/api/v1/proxy/voices")
    finally:
        client.app.dependency_overrides.clear()
    assert [v["voice_id"] for v in r.json()] == ["premade1"]


def test_synthesize_rejects_path_like_voice_id(client) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "pro"}
    quota = AsyncMock()
    try:
        with patch("app.routers.proxy.check_and_record_quota", quota):
            r = client.post(
                "/api/v1/proxy/synthesize/stream",
                json={"text": "hello", "voice_id": "../../voices/abc/edit"},
            )
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 422
    quota.assert_not_called()


def test_delete_voice_rejects_bad_id_before_upstream() -> None:
    import asyncio

    import pytest
    from fastapi import HTTPException

    with (
        patch("app.routers.proxy._elevenlabs", side_effect=AssertionError("upstream called")),
        pytest.raises(HTTPException) as exc,
    ):
        asyncio.run(proxy.delete_voice("bad id;x", user={"id": "u1"}))
    assert exc.value.status_code == 400


def test_transcribe_rejects_oversized_upload(client) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "pro"}
    quota = AsyncMock()
    try:
        with (
            patch("app.routers.proxy.MAX_AUDIO_UPLOAD_BYTES", 1000),
            patch("app.routers.proxy.check_and_record_quota", quota),
        ):
            r = client.post(
                "/api/v1/proxy/transcribe",
                files={"audio": ("a.wav", b"\x00" * 2000, "audio/wav")},
            )
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 413
    quota.assert_not_called()
