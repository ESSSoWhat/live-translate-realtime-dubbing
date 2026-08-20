"""Tests for ElevenLabs error mapping on the proxy."""

from __future__ import annotations

from app.routers.proxy import _elevenlabs_error_message, _elevenlabs_upstream_error


def test_compact_json_message_is_surfaced() -> None:
    """Compact JSON (no space after colon) must not become 'upstream error'."""
    exc = Exception(
        'status_code: 400, body: {"detail":{"status":"invalid_scribe",'
        '"message":"Could not identify a voice in the audio"}}'
    )
    err = _elevenlabs_upstream_error("Voice cloning", exc)
    assert err.status_code == 502
    assert "Could not identify a voice in the audio" in str(err.detail)


def test_pretty_json_message_is_surfaced() -> None:
    """Pretty-printed JSON with spaces still extracts the message."""
    exc = Exception(
        'status_code: 400, body: {\n  "detail": {\n    "message": "voice limit reached"\n  }\n}'
    )
    err = _elevenlabs_upstream_error("Voice cloning", exc)
    assert "voice limit reached" in str(err.detail)


def test_unknown_body_falls_back_to_upstream_error() -> None:
    """Unparseable bodies keep the generic label."""
    err = _elevenlabs_upstream_error("Voice cloning", Exception("status_code: 502, body: <html>"))
    assert str(err.detail) == "Voice cloning failed: upstream error"


def test_error_message_helper_reads_nested_detail() -> None:
    """Helper returns the nested ElevenLabs message."""
    text = 'body: {"detail":{"status":"detected_no_speech","message":"No speech detected"}}'
    assert _elevenlabs_error_message(text) == "No speech detected"
