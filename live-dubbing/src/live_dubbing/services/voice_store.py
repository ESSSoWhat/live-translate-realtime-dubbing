"""
Persistent storage for cloned voice metadata.

Saves voice information to a JSON file so cloned voices survive
app restarts.  ElevenLabs keeps the actual voice model on their
servers; we just remember the voice_id, name, and speaker label.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import structlog

from live_dubbing.services.voice_cloning import ClonedVoice

logger = structlog.get_logger(__name__)

# Default storage location
_DEFAULT_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "Live Translate",
)
_DEFAULT_PATH = os.path.join(_DEFAULT_DIR, "voices.json")


class VoiceStore:
    """Persists cloned voice metadata to a local JSON file.

    File format::

        {
            "voices": [
                {
                    "voice_id": "abc123",
                    "name": "Speaker A",
                    "speaker_label": "Speaker A",
                    "created_at": "2025-01-15T10:30:00",
                    "sample_duration_sec": 15.2,
                    "is_dynamic": true
                },
                ...
            ],
            "default_voice_id": "abc123"
        }
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    # ── Public API ───────────────────────────────────────────────────────

    def save(self, voice: ClonedVoice) -> None:
        """Add or update a voice in the store."""
        data = self._read()
        voices = data.get("voices", [])

        # Replace if voice_id already exists; otherwise append
        voices = [v for v in voices if v.get("voice_id") != voice.voice_id]
        voices.append(self._voice_to_dict(voice))

        data["voices"] = voices
        self._write(data)
        logger.info("Voice saved to store", voice_id=voice.voice_id, name=voice.name)

    def load_all(self) -> list[ClonedVoice]:
        """Load all saved voices."""
        data = self._read()
        voices: list[ClonedVoice] = []
        for entry in data.get("voices", []):
            try:
                voices.append(self._dict_to_voice(entry))
            except Exception:
                logger.warning("Skipping invalid voice entry", entry=entry)
        return voices

    def update_name(self, voice_id: str, new_name: str) -> bool:
        """Update the display name of a stored voice. Returns True if updated."""
        cleaned = (new_name or "").strip()
        if not cleaned:
            return False
        voices = self.load_all()
        for v in voices:
            if v.voice_id == voice_id:
                updated = ClonedVoice(
                    voice_id=v.voice_id,
                    name=cleaned,
                    created_at=v.created_at,
                    sample_duration_sec=v.sample_duration_sec,
                    is_dynamic=v.is_dynamic,
                    speaker_id=v.speaker_id,
                )
                self.save(updated)
                return True
        return False

    def delete(self, voice_id: str) -> None:
        """Remove a voice from the store."""
        data = self._read()
        voices = data.get("voices", [])
        data["voices"] = [v for v in voices if v.get("voice_id") != voice_id]

        # Clear default if it was this voice
        if data.get("default_voice_id") == voice_id:
            data["default_voice_id"] = None

        self._write(data)
        logger.info("Voice deleted from store", voice_id=voice_id)

    def get_default_voice_id(self) -> str | None:
        """Get the default voice ID (last used)."""
        data = self._read()
        return data.get("default_voice_id")

    def set_default_voice_id(self, voice_id: str | None) -> None:
        """Set the default voice ID."""
        data = self._read()
        data["default_voice_id"] = voice_id
        self._write(data)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _read(self) -> dict:
        """Read the JSON file, returning empty structure if missing/corrupt."""
        try:
            if Path(self._path).exists():
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read voice store", error=str(e))
        return {"voices": [], "default_voice_id": None}

    def _write(self, data: dict) -> None:
        """Write the JSON file atomically (write-then-rename)."""
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            # Atomic rename on Windows (replace if exists)
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.error("Failed to write voice store", error=str(e))

    @staticmethod
    def _voice_to_dict(voice: ClonedVoice) -> dict:
        return {
            "voice_id": voice.voice_id,
            "name": voice.name,
            "speaker_label": voice.speaker_id or voice.name,
            "created_at": voice.created_at.isoformat(),
            "sample_duration_sec": voice.sample_duration_sec,
            "is_dynamic": voice.is_dynamic,
        }

    @staticmethod
    def _dict_to_voice(d: dict) -> ClonedVoice:
        created = d.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except ValueError:
                created = datetime.now()
        elif not isinstance(created, datetime):
            created = datetime.now()

        return ClonedVoice(
            voice_id=d["voice_id"],
            name=d.get("name", "Unknown"),
            created_at=created,
            sample_duration_sec=d.get("sample_duration_sec", 0.0),
            is_dynamic=d.get("is_dynamic", False),
            speaker_id=d.get("speaker_label"),
        )
