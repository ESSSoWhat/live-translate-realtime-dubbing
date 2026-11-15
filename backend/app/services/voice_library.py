"""Helpers for the shared ElevenLabs voice library."""

from __future__ import annotations

import ast
import json
import re

# Older builds tried to stamp this label. ElevenLabs only accepts language,
# accent, gender, and age, so a custom key makes every clone fail.
APP_VOICE_LABEL = "live-translate"
# Empty object. This is the payload that last created voices successfully.
CLONE_LABELS = "{}"
# Auto clones used this name before the app label existed.
LEGACY_CLONE_PREFIX = "dynamic_clone_"
_SECRET_RE = re.compile(r"sk_[A-Za-z0-9_]+")


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


def _created_unix(voice: dict) -> int:
    try:
        return int(voice.get("created_at_unix") or 0)
    except (TypeError, ValueError):
        return 0


def _voice_id(voice: dict) -> str:
    return str(voice.get("voice_id") or "").strip()


def _is_app_clone(voice: dict) -> bool:
    """True for voices this backend created, including pre-label auto clones."""
    if str(voice.get("category") or "") != "cloned":
        return False
    if _created_by_this_app(voice):
        return True
    return str(voice.get("name") or "").startswith(LEGACY_CLONE_PREFIX)


def clone_ids_to_free(
    voices: list[dict],
    owned: set[str],
    user_owned: set[str],
    *,
    keep_for_user: int = 2,
    limit: int = 20,
) -> list[str]:
    """Voices this app may delete so a new clone can be added.

    Unowned app clones go first. Then this user's oldest app clones, leaving
    the newest ``keep_for_user``. Premade voices and clones made outside the
    app are left alone.
    """
    selected: list[str] = []
    seen: set[str] = set()

    def add(voice_id: str) -> None:
        if voice_id and voice_id not in seen and len(selected) < limit:
            seen.add(voice_id)
            selected.append(voice_id)

    for voice_id in orphan_cloned_voice_ids(voices, owned, limit=limit):
        add(voice_id)

    legacy: list[tuple[int, str]] = []
    mine: list[tuple[int, str]] = []
    for voice in voices:
        voice_id = _voice_id(voice)
        if not voice_id or not _is_app_clone(voice):
            continue
        created = _created_unix(voice)
        if voice_id not in owned:
            legacy.append((created, voice_id))
        elif voice_id in user_owned:
            mine.append((created, voice_id))

    for _, voice_id in sorted(legacy):
        add(voice_id)
    mine.sort()
    overflow = mine[:-keep_for_user] if len(mine) > keep_for_user else []
    for _, voice_id in overflow:
        add(voice_id)
    return selected


def elevenlabs_failure_message(raw: str) -> str:
    """Short ElevenLabs message from JSON or a Python-dict exception string."""
    snippet = raw.strip()
    body_at = snippet.lower().find("body:")
    if body_at >= 0:
        snippet = snippet[body_at + len("body:") :].strip()
    payload = _parse_failure_payload(snippet)
    message = ""
    if payload is not None:
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        message = _first_failure_text(detail)
    if not message:
        message = _quoted_message(raw)
    if not message:
        return ""
    return _SECRET_RE.sub("sk_…", message)[:180]


def _parse_failure_payload(snippet: str) -> object | None:
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        pass
    start = snippet.find("{")
    if start < 0:
        start = snippet.find("[")
    if start < 0:
        return None
    try:
        return ast.literal_eval(snippet[start:])
    except (SyntaxError, ValueError):
        return None


def _quoted_message(text: str) -> str:
    """Read a message field when the body is truncated and will not parse."""
    markers = (
        "'message': '",
        '"message": "',
        "'message': \"",
        '"message": \'',
        "'msg': '",
        '"msg": "',
    )
    for marker in markers:
        start = text.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = text.find(marker[-1], start)
        if end > start:
            return text[start:end].strip()
    return ""


def _first_failure_text(node: object) -> str:
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, dict):
        for key in ("message", "msg"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                status = node.get("status") or node.get("code")
                status_text = status.strip() if isinstance(status, str) else ""
                if status_text and status_text.lower() not in text.lower():
                    return f"{status_text}: {text}"
                return text
        for value in node.values():
            found = _first_failure_text(value)
            if found:
                return found
    if isinstance(node, list):
        for item in node:
            found = _first_failure_text(item)
            if found:
                return found
    return ""


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
