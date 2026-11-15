"""Helpers for the shared ElevenLabs voice library."""

from __future__ import annotations


def voice_limit_reached(body: str) -> bool:
    """True when ElevenLabs refused because the workspace voice library is full."""
    lower = body.lower()
    return "voice_limit_reached" in lower or "maximum amount of custom voices" in lower


def orphan_cloned_voice_ids(
    voices: list[dict],
    owned: set[str],
    limit: int = 80,
) -> list[str]:
    """Oldest cloned voices that no user still owns, capped at *limit*."""
    orphans: list[tuple[int, str]] = []
    for voice in voices:
        voice_id = str(voice.get("voice_id") or "").strip()
        category = str(voice.get("category") or "")
        if not voice_id or voice_id in owned or category != "cloned":
            continue
        created = voice.get("created_at_unix") or 0
        try:
            created_at = int(created)
        except (TypeError, ValueError):
            created_at = 0
        orphans.append((created_at, voice_id))
    orphans.sort()
    return [voice_id for _, voice_id in orphans[:limit]]
