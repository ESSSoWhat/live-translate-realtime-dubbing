"""Helpers for the shared ElevenLabs voice library."""

from __future__ import annotations

import json

# Written onto voices this backend creates. Cleanup may remove only these.
APP_VOICE_LABEL = "live-translate"


def voice_limit_reached(body: str) -> bool:
    """True when ElevenLabs refused because the workspace voice library is full."""
    lower = body.lower()
    return "voice_limit_reached" in lower or "maximum amount of custom voices" in lower


def _created_by_this_app(voice: dict) -> bool:
    """True only when this backend stamped the voice at creation."""
    labels = voice.get("labels")
    if isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except ValueError:
            return False
    if not isinstance(labels, dict):
        return False
    return str(labels.get("app") or "") == APP_VOICE_LABEL


def orphan_cloned_voice_ids(
    voices: list[dict],
    owned: set[str],
    limit: int = 80,
) -> list[str]:
    """Oldest app-created clones that no user still owns, capped at *limit*.

    Voices without this app's label are left alone, including clones made
    directly in the ElevenLabs workspace.
    """
    orphans: list[tuple[int, str]] = []
    for voice in voices:
        voice_id = str(voice.get("voice_id") or "").strip()
        category = str(voice.get("category") or "")
        if (
            not voice_id
            or voice_id in owned
            or category != "cloned"
            or not _created_by_this_app(voice)
        ):
            continue
        created = voice.get("created_at_unix") or 0
        try:
            created_at = int(created)
        except (TypeError, ValueError):
            created_at = 0
        orphans.append((created_at, voice_id))
    orphans.sort()
    return [voice_id for _, voice_id in orphans[:limit]]
