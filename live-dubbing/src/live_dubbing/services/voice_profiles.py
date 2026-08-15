"""
Persistent voice profiles for MFCC speaker matching and TTS voice selection.

Profiles group detected speakers and optionally point at a cloned ElevenLabs
voice_id. Embeddings are stored so matching survives app restarts.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "Live Translate",
)
_DEFAULT_PATH = os.path.join(_DEFAULT_DIR, "voice_profiles.json")

MAX_AUTO_PROFILES = 8


@dataclass
class VoiceProfile:
    """A named speaker profile with optional TTS voice assignment."""

    id: str
    name: str
    assigned_voice_id: str | None = None
    embedding: list[float] | None = None
    created_at: datetime = field(default_factory=datetime.now)


class VoiceProfileStore:
    """Persists voice profiles to a local JSON file."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def load_all(self) -> list[VoiceProfile]:
        """Load all saved profiles."""
        data = self._read()
        profiles: list[VoiceProfile] = []
        for entry in data.get("profiles", []):
            try:
                profiles.append(self._dict_to_profile(entry))
            except Exception:
                logger.warning("Skipping invalid profile entry", entry=entry)
        return profiles

    def get(self, profile_id: str) -> VoiceProfile | None:
        """Get a single profile by id."""
        for p in self.load_all():
            if p.id == profile_id:
                return p
        return None

    def save(self, profile: VoiceProfile) -> None:
        """Add or update a profile."""
        data = self._read()
        profiles = data.get("profiles", [])
        profiles = [p for p in profiles if p.get("id") != profile.id]
        profiles.append(self._profile_to_dict(profile))
        data["profiles"] = profiles
        self._write(data)
        logger.info("Profile saved", profile_id=profile.id, name=profile.name)

    def delete(self, profile_id: str) -> None:
        """Remove a profile. Clears default if it was this profile."""
        data = self._read()
        data["profiles"] = [p for p in data.get("profiles", []) if p.get("id") != profile_id]
        if data.get("default_profile_id") == profile_id:
            data["default_profile_id"] = None
        self._write(data)
        logger.info("Profile deleted", profile_id=profile_id)

    def get_default_profile_id(self) -> str | None:
        """Get the default profile id used when identification fails."""
        return self._read().get("default_profile_id")

    def set_default_profile_id(self, profile_id: str | None) -> None:
        """Set the default profile id."""
        data = self._read()
        data["default_profile_id"] = profile_id
        self._write(data)

    def clear_voice_assignments(self, voice_id: str) -> None:
        """Clear assigned_voice_id on any profile pointing at *voice_id*."""
        data = self._read()
        changed = False
        for entry in data.get("profiles", []):
            if entry.get("assigned_voice_id") == voice_id:
                entry["assigned_voice_id"] = None
                changed = True
        if changed:
            self._write(data)

    @staticmethod
    def new_id() -> str:
        """Generate a new profile id."""
        return f"profile_{uuid.uuid4().hex[:12]}"

    def _read(self) -> dict:
        try:
            if Path(self._path).exists():
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read voice profile store", error=str(e))
        return {"profiles": [], "default_profile_id": None}

    def _write(self, data: dict) -> None:
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.error("Failed to write voice profile store", error=str(e))

    @staticmethod
    def _profile_to_dict(profile: VoiceProfile) -> dict:
        return {
            "id": profile.id,
            "name": profile.name,
            "assigned_voice_id": profile.assigned_voice_id,
            "embedding": profile.embedding,
            "created_at": profile.created_at.isoformat(),
        }

    @staticmethod
    def _dict_to_profile(d: dict) -> VoiceProfile:
        created = d.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except ValueError:
                created = datetime.now()
        elif not isinstance(created, datetime):
            created = datetime.now()

        embedding = d.get("embedding")
        if embedding is not None and not isinstance(embedding, list):
            embedding = None

        return VoiceProfile(
            id=d["id"],
            name=d.get("name", "Speaker"),
            assigned_voice_id=d.get("assigned_voice_id"),
            embedding=embedding,
            created_at=created,
        )
