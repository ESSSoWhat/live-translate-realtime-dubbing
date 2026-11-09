"""Character cards shared with Dubtool.

A card is a folder of voice.json plus reference.wav. Dubtool matches the
voice from the recording. Live Translate also stores its ElevenLabs voice id
so a card can be reused without cloning it again.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import wave
from pathlib import Path

import numpy as np

_CARD_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def cards_dir() -> Path:
    """Folder both apps read. Dubtool uses this when it is not in a test sandbox."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / "Live Translate" / "voices"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_cards() -> list[dict]:
    """Saved cards, by name, with the recording path filled in."""
    found: list[dict] = []
    root = cards_dir()
    if not root.is_dir():
        return found
    for folder in root.iterdir():
        meta = folder / "voice.json"
        reference = folder / "reference.wav"
        if not meta.is_file() or not reference.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        card_id = str(data.get("id") or "")
        if not _CARD_ID.fullmatch(card_id):
            continue
        data["reference"] = str(reference)
        data["name"] = str(data.get("name") or "Character")
        found.append(data)
    return sorted(found, key=lambda card: card["name"].lower())


def publish_card(
    card_id: str,
    name: str,
    audio: np.ndarray,
    sample_rate: int,
    elevenlabs_voice_id: str | None = None,
) -> str:
    """Write or update one card. An existing Dubtool voice match is kept."""
    chosen = card_id if _CARD_ID.fullmatch(card_id or "") else uuid.uuid4().hex[:12]
    folder = cards_dir() / chosen
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "reference.wav"
    _write_wav(destination, audio, sample_rate)
    data: dict = {}
    meta = folder / "voice.json"
    if meta.is_file():
        try:
            loaded = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data["id"] = chosen
    data["name"] = (name or "").strip() or "Character"
    data.setdefault("embedding", [])
    data["sources"] = [str(destination)]
    if elevenlabs_voice_id:
        data["elevenlabs_voice_id"] = elevenlabs_voice_id
    meta.write_text(json.dumps(data), encoding="utf-8")
    return chosen


def rename_card(card_id: str, name: str) -> None:
    """Keep the shared card's name in step with the profile."""
    if not _CARD_ID.fullmatch(card_id or ""):
        return
    cleaned = (name or "").strip()
    if not cleaned:
        return
    meta = cards_dir() / card_id / "voice.json"
    if not meta.is_file():
        return
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    data["name"] = cleaned
    meta.write_text(json.dumps(data), encoding="utf-8")


def delete_card(card_id: str) -> bool:
    """Remove a saved card. Profiles that already loaded it keep their voice."""
    if not _CARD_ID.fullmatch(card_id or ""):
        return False
    folder = cards_dir() / card_id
    if not (folder / "voice.json").is_file():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
