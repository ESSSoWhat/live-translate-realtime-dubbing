"""Orphan voice selection when the ElevenLabs library is full."""

from app.services.voice_library import orphan_cloned_voice_ids, voice_limit_reached


def test_voice_limit_detected_from_upstream_body() -> None:
    body = '{"detail":{"status":"voice_limit_reached","message":"maximum amount of custom voices"}}'
    assert voice_limit_reached(body)
    assert not voice_limit_reached('{"detail":{"status":"invalid_audio"}}')


def test_orphan_clones_skip_owned_and_premade() -> None:
    voices = [
        {"voice_id": "old", "category": "cloned", "created_at_unix": 10},
        {"voice_id": "kept", "category": "cloned", "created_at_unix": 5},
        {"voice_id": "new", "category": "cloned", "created_at_unix": 20},
        {"voice_id": "rachel", "category": "premade", "created_at_unix": 1},
        {"voice_id": "", "category": "cloned", "created_at_unix": 1},
    ]
    picked = orphan_cloned_voice_ids(voices, {"kept"}, limit=10)
    assert picked == ["old", "new"]
