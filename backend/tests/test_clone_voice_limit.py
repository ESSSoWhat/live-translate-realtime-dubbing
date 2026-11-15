"""Voice-library cleanup and ElevenLabs error text for clone failures."""

from app.services.voice_library import (
    CLONE_LABELS,
    clone_ids_to_free,
    elevenlabs_failure_message,
    orphan_cloned_voice_ids,
    voice_limit_reached,
)


def test_clone_labels_stay_empty_so_elevenlabs_accepts_them() -> None:
    assert CLONE_LABELS == "{}"
    assert "app" not in CLONE_LABELS


def test_voice_limit_detected_from_upstream_body() -> None:
    body = '{"detail":{"status":"voice_limit_reached","message":"maximum amount of custom voices"}}'
    assert voice_limit_reached(body)
    assert not voice_limit_reached('{"detail":{"status":"invalid_audio"}}')


def test_orphan_clones_skip_owned_premade_and_foreign() -> None:
    app = {"app": "live-translate"}
    voices = [
        {"voice_id": "old", "category": "cloned", "created_at_unix": 10, "labels": app},
        {"voice_id": "kept", "category": "cloned", "created_at_unix": 5, "labels": app},
        {"voice_id": "new", "category": "cloned", "created_at_unix": 20, "labels": app},
        {"voice_id": "rachel", "category": "premade", "created_at_unix": 1, "labels": app},
        {"voice_id": "outside", "category": "cloned", "created_at_unix": 2, "labels": {}},
        {"voice_id": "", "category": "cloned", "created_at_unix": 1, "labels": app},
    ]
    picked = orphan_cloned_voice_ids(voices, {"kept"}, limit=10)
    assert picked == ["old", "new"]


def test_frees_legacy_auto_clones_and_oldest_owned_ones() -> None:
    app = {"app": "live-translate"}
    voices = [
        {"voice_id": "legacy", "name": "dynamic_clone_1", "category": "cloned", "created_at_unix": 1},
        {"voice_id": "foreign", "name": "studio take", "category": "cloned", "created_at_unix": 2},
        {"voice_id": "oldest", "name": "dynamic_clone_2", "category": "cloned", "created_at_unix": 3, "labels": app},
        {"voice_id": "middle", "name": "dynamic_clone_3", "category": "cloned", "created_at_unix": 4, "labels": app},
        {"voice_id": "newest", "name": "dynamic_clone_4", "category": "cloned", "created_at_unix": 5, "labels": app},
        {"voice_id": "rachel", "name": "Rachel", "category": "premade", "created_at_unix": 1},
    ]
    picked = clone_ids_to_free(
        voices,
        owned={"oldest", "middle", "newest"},
        user_owned={"oldest", "middle", "newest"},
        keep_for_user=2,
    )
    assert picked == ["legacy", "oldest"]


def test_keeps_a_small_personal_library_when_nothing_else_is_ours() -> None:
    voices = [
        {"voice_id": "only", "name": "dynamic_clone_9", "category": "cloned", "created_at_unix": 1},
        {"voice_id": "foreign", "name": "manual", "category": "cloned", "created_at_unix": 2},
    ]
    assert clone_ids_to_free(voices, {"only"}, {"only"}, keep_for_user=2) == []


def test_failure_message_reads_compact_json() -> None:
    body = '{"detail":{"status":"invalid_audio","message":"Audio file is too short"}}'
    wrapped = f"status_code: 400, body: {body}"
    assert elevenlabs_failure_message(wrapped) == "invalid_audio: Audio file is too short"


def test_failure_message_redacts_secrets() -> None:
    body = '{"detail":{"message":"bad key sk_live_secretvalue"}}'
    assert "sk_live_secretvalue" not in elevenlabs_failure_message(body)
    assert "sk_…" in elevenlabs_failure_message(body)


def test_upstream_error_is_not_generic_when_elevenlabs_sent_a_message() -> None:
    from app.routers.proxy import _elevenlabs_upstream_error

    body = '{"detail":{"status":"invalid_audio","message":"Audio file is too short"}}'
    exc = _elevenlabs_upstream_error(
        "Voice cloning",
        Exception(f"status_code: 400, body: {body}"),
    )
    assert exc.status_code == 502
    assert exc.detail == "Voice cloning failed: invalid_audio: Audio file is too short"


def test_voice_limit_message_says_the_library_is_full() -> None:
    from app.routers.proxy import _elevenlabs_upstream_error

    body = '{"detail":{"status":"voice_limit_reached","message":"maximum amount of custom voices"}}'
    exc = _elevenlabs_upstream_error(
        "Voice cloning",
        Exception(f"status_code: 400, body: {body}"),
    )
    assert "voice library is full" in str(exc.detail)
    assert "upstream error" not in str(exc.detail)
