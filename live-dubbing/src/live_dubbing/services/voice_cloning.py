"""
Voice cloning management for dynamic voice capture.

Supports multiple speaker voices, persistent caching, and manual
speaker selection.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import structlog

from live_dubbing.services.elevenlabs_service import ElevenLabsService
from live_dubbing.services.voice_profiles import (
    MAX_AUTO_PROFILES,
    VoiceProfile,
    VoiceProfileStore,
)

if TYPE_CHECKING:
    from live_dubbing.services.voice_store import VoiceStore

logger = structlog.get_logger(__name__)

# Premade Rachel voice used when no profile has an assigned clone.
_FALLBACK_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


@dataclass
class ClonedVoice:
    """Information about a cloned voice."""

    voice_id: str
    name: str
    created_at: datetime = field(default_factory=datetime.now)
    sample_duration_sec: float = 0.0
    is_dynamic: bool = False
    speaker_id: str | None = None
    reused_existing: bool = False


class VoiceCloneManager:
    """
    Manages voice cloning workflow.

    Supports:
    - Dynamic voice cloning from captured audio
    - Multiple speaker voices (manual capture per speaker)
    - Persistent voice caching via VoiceStore
    - Automatic cleanup of temporary voices
    """

    def __init__(
        self,
        elevenlabs_service: ElevenLabsService,
        min_sample_duration_sec: float = 30.0,
        max_sample_duration_sec: float = 120.0,
        voice_store: VoiceStore | None = None,
        profile_store: VoiceProfileStore | None = None,
    ) -> None:
        """
        Initialize voice clone manager.

        Args:
            elevenlabs_service: ElevenLabs API service
            min_sample_duration_sec: Minimum audio duration for cloning
            max_sample_duration_sec: Maximum audio to capture for cloning
            voice_store: Optional persistent store for voice metadata
            profile_store: Optional persistent store for voice profiles
        """
        # Lazy import to avoid circular import:
        # voice_cloning → processing.speaker_id → processing.__init__
        # → processing.pipeline → voice_cloning (not done yet!)
        from live_dubbing.processing.speaker_id import SpeakerIdentifier  # noqa: PLC0415

        self._service = elevenlabs_service
        self._min_sample_duration = min_sample_duration_sec
        self._max_sample_duration = max_sample_duration_sec
        self._voice_store = voice_store
        self._profile_store = profile_store or VoiceProfileStore()
        self._max_auto_profiles = MAX_AUTO_PROFILES

        # Cache of cloned voices (voice_id → ClonedVoice)
        self._voice_cache: dict[str, ClonedVoice] = {}

        # In-memory profiles (profile_id → VoiceProfile)
        self._profiles: dict[str, VoiceProfile] = {}
        self._default_profile_id: str | None = None
        self._active_profile_id: str | None = None

        # Speaker identification (MFCC-based); keys are profile_ids
        self._speaker_id = SpeakerIdentifier(sample_rate=16000)

        # Audio buffer for dynamic cloning
        self._audio_buffer: list[np.ndarray] = []
        self._buffer_duration_sec = 0.0
        self._is_capturing = False
        self._capture_speaker_label: str | None = None
        self._capture_profile_id: str | None = None
        self._sample_rate = 16000
        self._buffer_lock = asyncio.Lock()
        self._clone_lock = asyncio.Lock()
        # Accumulate short lines until Instant Voice Clone has enough speech.
        self._line_clone_parts: list[np.ndarray] = []
        self._line_clone_duration_sec = 0.0

        # Load previously saved voices from persistent store
        if self._voice_store:
            try:
                for voice in self._voice_store.load_all():
                    self._voice_cache[voice.voice_id] = voice
                if self._voice_cache:
                    logger.info(
                        "Loaded saved voices",
                        count=len(self._voice_cache),
                    )
            except Exception as e:
                logger.warning("Could not load saved voices", error=str(e))

        self._load_profiles()

    def _load_profiles(self) -> None:
        """Load profiles from disk and rehydrate MFCC embeddings."""
        try:
            for profile in self._profile_store.load_all():
                self._profiles[profile.id] = profile
                if profile.embedding:
                    self._speaker_id.register_embedding(profile.id, profile.embedding)
            self._default_profile_id = self._profile_store.get_default_profile_id()
            if self._default_profile_id and self._default_profile_id not in self._profiles:
                self._default_profile_id = None
            if self._profiles:
                logger.info(
                    "Loaded voice profiles",
                    count=len(self._profiles),
                    default_profile_id=self._default_profile_id,
                )
        except Exception as e:
            logger.warning("Could not load voice profiles", error=str(e))

    async def start_dynamic_capture(
        self,
        sample_rate: int = 16000,
        speaker_label: str | None = None,
        profile_id: str | None = None,
    ) -> None:
        """
        Start capturing audio for dynamic voice cloning.

        Args:
            sample_rate: Sample rate of incoming audio
            speaker_label: Label for the speaker being captured
            profile_id: Optional existing profile to bind the clone to
        """
        self._audio_buffer = []
        self._buffer_duration_sec = 0.0
        self._is_capturing = True
        self._sample_rate = sample_rate
        self._capture_speaker_label = speaker_label
        self._capture_profile_id = profile_id

        logger.info(
            "Started dynamic voice capture",
            speaker_label=speaker_label,
            profile_id=profile_id,
        )

    def add_audio_chunk(self, audio: np.ndarray) -> bool:
        """
        Add audio chunk to capture buffer.

        Args:
            audio: Audio data as numpy array

        Returns:
            True if enough audio captured for cloning
        """
        if not self._is_capturing:
            return False

        self._audio_buffer.append(audio)
        chunk_duration = len(audio) / self._sample_rate
        self._buffer_duration_sec += chunk_duration

        # Check if we have enough audio
        if self._buffer_duration_sec >= self._min_sample_duration:
            logger.info(
                "Enough audio captured for cloning",
                duration_sec=self._buffer_duration_sec,
            )
            return True

        # Stop if we've captured too much
        if self._buffer_duration_sec >= self._max_sample_duration:
            logger.warning(
                "Max capture duration reached",
                duration_sec=self._buffer_duration_sec,
            )
            return True

        return False

    async def create_dynamic_clone(
        self,
        name: str | None = None,
        speaker_label: str | None = None,
    ) -> ClonedVoice:
        """
        Create voice clone from captured audio.

        Args:
            name: Optional name for the voice
            speaker_label: Label for this speaker (overrides capture label)

        Returns:
            ClonedVoice with voice ID
        """
        async with self._buffer_lock:
            if not self._audio_buffer:
                raise RuntimeError("No audio captured for cloning")

            # Combine audio buffer
            combined_audio = np.concatenate(self._audio_buffer)
            buffer_duration = self._buffer_duration_sec

            # Resolve speaker label / profile binding
            label = speaker_label or self._capture_speaker_label
            link_profile_id = self._capture_profile_id

            # Clear buffer
            self._audio_buffer = []
            self._buffer_duration_sec = 0.0
            self._is_capturing = False
            self._capture_speaker_label = None
            self._capture_profile_id = None

        return await self._clone_from_samples(
            combined_audio,
            buffer_duration,
            name=name,
            label=label,
            link_profile_id=link_profile_id,
        )

    def get_voice(self, voice_id: str) -> ClonedVoice | None:
        """Return a cached clone by id, if present."""
        return self._voice_cache.get(voice_id)

    def reset_line_clone_buffer(self) -> None:
        """Clear speech held for the next Instant Voice Clone."""
        self._line_clone_parts = []
        self._line_clone_duration_sec = 0.0

    def _min_line_clone_sec(self) -> float:
        """Seconds of speech required before creating a new Instant clone."""
        configured = float(self._min_sample_duration)
        # Production mins are 3s+; honor lower values used by tests.
        if configured < 3.0:
            return max(0.5, configured)
        return min(configured, 8.0)

    async def voice_for_utterance(self, audio: np.ndarray) -> ClonedVoice | None:
        """Reuse a matching profile voice, or clone after enough speech.

        Short lines alone make poor Instant Voice Clones. Matching profiles
        still return immediately. New speakers accumulate speech until the
        capture minimum is reached, then one clone is created from that buffer.
        """
        speech = np.asarray(audio, dtype=np.float32).reshape(-1)
        if speech.size < int(self._sample_rate * 0.5):
            return None

        matched, score = self._similar_profile(speech)
        if matched is not None:
            reused = self._reuse_assigned_voice(matched, speech)
            if reused is not None:
                self.reset_line_clone_buffer()
                return reused

        duration = float(speech.size) / self._sample_rate
        self._line_clone_parts.append(speech)
        self._line_clone_duration_sec += duration

        if self._line_clone_duration_sec < self._min_line_clone_sec():
            logger.info(
                "Waiting for more speech before cloning",
                buffered_sec=round(self._line_clone_duration_sec, 2),
                needed_sec=round(self._min_line_clone_sec(), 2),
                last_match_score=round(score, 3),
            )
            return None

        combined = np.concatenate(self._line_clone_parts)
        buffered = self._line_clone_duration_sec
        self.reset_line_clone_buffer()
        return await self._clone_from_samples(
            combined,
            buffered,
            link_profile_id=matched.id if matched is not None else None,
            label=matched.name if matched is not None else None,
        )

    async def _clone_from_samples(
        self,
        combined_audio: np.ndarray,
        buffer_duration: float,
        *,
        name: str | None = None,
        label: str | None = None,
        link_profile_id: str | None = None,
    ) -> ClonedVoice:
        """Turn one audio clip into a cloned voice, reusing a similar profile."""
        async with self._clone_lock:
            matched, _score = self._similar_profile(combined_audio)
            if matched is not None:
                reused = self._reuse_assigned_voice(matched, combined_audio)
                if reused is not None:
                    return reused
                link_profile_id = matched.id
                label = label or matched.name

            audio_bytes = self._audio_to_wav(combined_audio)
            if not name:
                name = label or f"line_clone_{time.time_ns()}"

            voice_id = await self._service.clone_voice(
                audio_data=audio_bytes,
                name=name,
                description=f"Cloned voice for speaker: {label or name}",
            )

            cloned_voice = ClonedVoice(
                voice_id=voice_id,
                name=name,
                sample_duration_sec=buffer_duration,
                is_dynamic=True,
                speaker_id=label,
            )
            self._voice_cache[voice_id] = cloned_voice
            self._link_clone_to_profile(
                audio=combined_audio,
                voice_id=voice_id,
                label=label or name,
                profile_id=link_profile_id,
            )
            if self._voice_store:
                try:
                    self._voice_store.save(cloned_voice)
                except Exception as e:
                    logger.warning("Could not persist voice", error=str(e))

            logger.info(
                "Voice cloned from line",
                voice_id=voice_id,
                speaker_label=label,
                duration_sec=cloned_voice.sample_duration_sec,
            )
            return cloned_voice

    async def create_clone_from_file(
        self,
        file_path: str,
        name: str | None = None,
        speaker_label: str | None = None,
    ) -> ClonedVoice:
        """
        Create voice clone from an audio file.

        Args:
            file_path: Path to audio file
            name: Optional name for the voice
            speaker_label: Label for this speaker

        Returns:
            ClonedVoice with voice ID
        """
        import os

        if not name:
            name = os.path.splitext(os.path.basename(file_path))[0]

        file_audio = self._read_mono_audio(file_path)
        link_profile_id: str | None = None
        if file_audio is not None:
            matched, _score = self._similar_profile(file_audio)
            if matched is not None:
                reused = self._reuse_assigned_voice(matched, file_audio)
                if reused is not None:
                    return reused
                link_profile_id = matched.id
                speaker_label = speaker_label or matched.name

        voice_id = await self._service.clone_voice_from_file(
            file_path=file_path,
            name=name,
        )

        cloned_voice = ClonedVoice(
            voice_id=voice_id,
            name=name,
            is_dynamic=False,
            speaker_id=speaker_label or name,
        )

        self._voice_cache[voice_id] = cloned_voice

        if file_audio is not None:
            self._link_clone_to_profile(
                audio=file_audio,
                voice_id=voice_id,
                label=speaker_label or name,
                profile_id=link_profile_id,
            )

        # Persist to store
        if self._voice_store:
            try:
                self._voice_store.save(cloned_voice)
            except Exception as e:
                logger.warning("Could not persist voice", error=str(e))

        return cloned_voice

    def _audio_to_wav(self, audio: np.ndarray) -> bytes:
        """Convert numpy audio to WAV bytes."""
        import io
        import wave

        # Ensure audio is float32 in range [-1, 1]
        audio = np.clip(audio, -1.0, 1.0)

        # Convert to 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)

        # Create WAV file in memory
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        buffer.seek(0)
        return buffer.read()

    def get_cached_voice(self, voice_id: str) -> ClonedVoice | None:
        """Get a cached voice by ID."""
        return self._voice_cache.get(voice_id)

    def get_voice_by_speaker(self, speaker_label: str) -> ClonedVoice | None:
        """Find a cached voice by its speaker label."""
        for voice in self._voice_cache.values():
            if voice.speaker_id == speaker_label:
                return voice
        return None

    def get_all_cached_voices(self) -> list[ClonedVoice]:
        """Get all cached voices."""
        return list(self._voice_cache.values())

    def rename_voice(self, voice_id: str, new_name: str) -> bool:
        """
        Rename a cloned voice (display name only).

        Args:
            voice_id: Voice ID to rename
            new_name: New display name

        Returns:
            True if the voice was found and renamed
        """
        voice = self._voice_cache.get(voice_id)
        if not voice:
            return False
        name = new_name.strip()
        if not name:
            return False
        if self._voice_store:
            try:
                if not self._voice_store.update_name(voice_id, name):
                    return False
            except Exception as e:
                logger.warning("Failed to update voice name in store", voice_id=voice_id, error=str(e))
                return False
        self._voice_cache[voice_id] = replace(voice, name=name)
        logger.info("Voice renamed", voice_id=voice_id, new_name=name)
        return True

    async def cleanup_voice(self, voice_id: str) -> bool:
        """
        Delete a cloned voice.

        Args:
            voice_id: Voice ID to delete

        Returns:
            True if successful
        """
        success = await self._service.delete_voice(voice_id)

        if success and voice_id in self._voice_cache:
            del self._voice_cache[voice_id]

        # Clear profile assignments pointing at this voice
        try:
            self._profile_store.clear_voice_assignments(voice_id)
            for profile in self._profiles.values():
                if profile.assigned_voice_id == voice_id:
                    profile.assigned_voice_id = None
        except Exception as e:
            logger.warning("Could not clear profile voice assignments", error=str(e))

        # Remove from persistent store
        if success and self._voice_store:
            try:
                self._voice_store.delete(voice_id)
            except Exception as e:
                logger.warning("Could not remove voice from store", error=str(e))

        return success

    async def cleanup_all_dynamic_voices(self) -> int:
        """
        Delete all dynamically cloned voices.

        Returns:
            Number of voices deleted
        """
        count = 0
        voices_to_delete = [
            v for v in self._voice_cache.values() if v.is_dynamic
        ]

        for voice in voices_to_delete:
            if await self.cleanup_voice(voice.voice_id):
                count += 1

        logger.info("Cleaned up dynamic voices", count=count)
        return count

    @property
    def is_capturing(self) -> bool:
        """Check if currently capturing for dynamic clone."""
        return self._is_capturing

    @property
    def capture_speaker_label(self) -> str | None:
        """Get the speaker label for the current capture."""
        return self._capture_speaker_label

    @property
    def capture_duration_sec(self) -> float:
        """Get current capture duration."""
        return self._buffer_duration_sec

    @property
    def capture_progress(self) -> float:
        """Get capture progress (0.0 to 1.0)."""
        if not self._is_capturing:
            return 0.0
        return min(1.0, self._buffer_duration_sec / self._min_sample_duration)

    def cancel_capture(self) -> None:
        """Cancel current dynamic capture."""
        self._audio_buffer = []
        self._buffer_duration_sec = 0.0
        self._is_capturing = False
        self._capture_speaker_label = None
        self._capture_profile_id = None
        logger.info("Dynamic capture cancelled")

    def identify_speaker(self, audio: np.ndarray) -> tuple[str | None, float]:
        """Identify which registered profile matches *audio*.

        Returns:
            ``(profile_id, confidence)`` or ``(None, score)`` when below threshold.
        """
        return self._speaker_id.identify(audio)

    @property
    def can_identify_speakers(self) -> bool:
        """True when at least one profile embedding is registered."""
        return self._speaker_id.has_speakers

    # ── Voice profiles ───────────────────────────────────────────────────

    def list_profiles(self) -> list[VoiceProfile]:
        """Return all voice profiles (copy of current cache)."""
        return list(self._profiles.values())

    def get_profile(self, profile_id: str) -> VoiceProfile | None:
        """Get a profile by id."""
        return self._profiles.get(profile_id)

    def get_default_profile(self) -> VoiceProfile | None:
        """Return the default profile, if set and present."""
        if self._default_profile_id:
            return self._profiles.get(self._default_profile_id)
        return None

    def get_default_profile_id(self) -> str | None:
        return self._default_profile_id

    def get_active_profile_id(self) -> str | None:
        return self._active_profile_id

    def set_default_profile(self, profile_id: str | None) -> bool:
        """Set the default profile used when identification fails."""
        if profile_id is not None and profile_id not in self._profiles:
            return False
        self._default_profile_id = profile_id
        try:
            self._profile_store.set_default_profile_id(profile_id)
        except Exception as e:
            logger.warning("Could not persist default profile", error=str(e))
            return False
        return True

    def set_profile_voice(self, profile_id: str, voice_id: str | None) -> bool:
        """Assign a cloned voice to a profile (or clear with None)."""
        profile = self._profiles.get(profile_id)
        if not profile:
            return False
        if voice_id is not None and voice_id not in self._voice_cache:
            return False
        profile.assigned_voice_id = voice_id
        try:
            self._profile_store.save(profile)
        except Exception as e:
            logger.warning("Could not persist profile voice", error=str(e))
            return False
        return True

    def rename_profile(self, profile_id: str, new_name: str) -> bool:
        """Rename a voice profile."""
        profile = self._profiles.get(profile_id)
        if not profile:
            return False
        name = new_name.strip()
        if not name:
            return False
        profile.name = name
        try:
            self._profile_store.save(profile)
        except Exception as e:
            logger.warning("Could not persist profile rename", error=str(e))
            return False
        return True

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile and unregister its embedding."""
        if profile_id not in self._profiles:
            return False
        del self._profiles[profile_id]
        self._speaker_id.unregister_speaker(profile_id)
        if self._default_profile_id == profile_id:
            self._default_profile_id = None
        if self._active_profile_id == profile_id:
            self._active_profile_id = None
        try:
            self._profile_store.delete(profile_id)
        except Exception as e:
            logger.warning("Could not delete profile from store", error=str(e))
            return False
        return True

    def resolve_tts_voice(self, profile: VoiceProfile | None) -> ClonedVoice | None:
        """Resolve the TTS clone for a profile, falling back to default / Rachel."""
        if profile and profile.assigned_voice_id:
            voice = self._voice_cache.get(profile.assigned_voice_id)
            if voice:
                return voice

        default = self.get_default_profile()
        if default and default.assigned_voice_id:
            voice = self._voice_cache.get(default.assigned_voice_id)
            if voice:
                return voice

        return ClonedVoice(
            voice_id=_FALLBACK_VOICE_ID,
            name="Rachel",
            is_dynamic=False,
        )

    def resolve_profile_for_audio(
        self,
        audio: np.ndarray,
        *,
        auto_create: bool = True,
    ) -> tuple[VoiceProfile | None, float]:
        """Match audio to a profile; optionally auto-create when unmatched.

        Returns ``(profile, confidence)``. On no match, returns the default
        profile (if any) with the raw match score. Auto-created profiles are
        returned as the matched profile with confidence 0.
        """
        profile_id, confidence = self._speaker_id.identify(audio)

        if profile_id and profile_id in self._profiles:
            profile = self._profiles[profile_id]
            self._active_profile_id = profile.id
            return profile, confidence

        if auto_create and len(audio) >= self._sample_rate * 0.5:
            created = self._maybe_auto_create_profile(audio)
            if created is not None:
                self._active_profile_id = created.id
                return created, 0.0

        default = self.get_default_profile()
        self._active_profile_id = default.id if default else None
        return default, confidence

    def _maybe_auto_create_profile(self, audio: np.ndarray) -> VoiceProfile | None:
        """Create a capped auto profile for an unmatched speaker."""
        if len(self._profiles) >= self._max_auto_profiles:
            logger.debug(
                "Auto profile cap reached",
                count=len(self._profiles),
                cap=self._max_auto_profiles,
            )
            return None

        next_num = self._next_speaker_number()
        profile = VoiceProfile(
            id=VoiceProfileStore.new_id(),
            name=f"Speaker {next_num}",
            assigned_voice_id=None,
        )
        emb = self._speaker_id.register_speaker(profile.id, audio)
        if emb is None:
            return None
        profile.embedding = emb.tolist()
        self._profiles[profile.id] = profile
        if self._default_profile_id is None:
            self._default_profile_id = profile.id
            try:
                self._profile_store.set_default_profile_id(profile.id)
            except Exception as e:
                logger.warning("Could not set default profile", error=str(e))
        try:
            self._profile_store.save(profile)
        except Exception as e:
            logger.warning("Could not persist auto profile", error=str(e))
        logger.info("Auto-created voice profile", profile_id=profile.id, name=profile.name)
        return profile

    def _next_speaker_number(self) -> int:
        """Pick the next 'Speaker N' index from existing names."""
        used: set[int] = set()
        for p in self._profiles.values():
            if p.name.startswith("Speaker "):
                suffix = p.name[len("Speaker ") :].strip()
                if suffix.isdigit():
                    used.add(int(suffix))
        n = 1
        while n in used:
            n += 1
        return n

    def clone_event_data(self, voice: ClonedVoice) -> dict[str, str | bool]:
        """Event payload for a finished clone, including a reused profile."""
        data: dict[str, str | bool] = {
            "voice_id": voice.voice_id,
            "name": voice.name,
            "reused": voice.reused_existing,
        }
        if not voice.reused_existing or not self._active_profile_id:
            return data
        profile = self._profiles.get(self._active_profile_id)
        if profile is None:
            return data
        data["profile_id"] = profile.id
        data["profile_name"] = profile.name
        data["name"] = profile.name
        return data

    def _similar_profile(self, audio: np.ndarray) -> tuple[VoiceProfile | None, float]:
        """Return the existing profile this clip already sounds like."""
        if len(audio) < int(self._sample_rate * 0.5):
            return None, 0.0
        profile_id, score = self._speaker_id.identify(audio)
        if profile_id and profile_id in self._profiles:
            return self._profiles[profile_id], score
        return None, score

    def _reuse_assigned_voice(
        self,
        profile: VoiceProfile,
        audio: np.ndarray,
    ) -> ClonedVoice | None:
        """Blend *audio* into *profile* and return its voice when one is assigned."""
        voice_id = profile.assigned_voice_id
        if not voice_id:
            return None
        voice = self._voice_cache.get(voice_id)
        if voice is None:
            return None
        self._combine_profile_embedding(profile, audio)
        self._profiles[profile.id] = profile
        self._active_profile_id = profile.id
        try:
            self._profile_store.save(profile)
        except Exception as e:
            logger.warning("Could not persist combined profile", error=str(e))
        logger.info(
            "Combined sample into existing profile",
            profile_id=profile.id,
            profile_name=profile.name,
            voice_id=voice_id,
            samples=profile.embedding_samples,
        )
        return replace(voice, reused_existing=True)

    def _combine_profile_embedding(self, profile: VoiceProfile, audio: np.ndarray) -> None:
        """Running-mean the new clip into the profile voiceprint."""
        if len(audio) < int(self._sample_rate * 0.5):
            return
        prior = profile.embedding_samples if profile.embedding else 0
        blended = self._speaker_id.blend_speaker(
            profile.id,
            audio,
            prior_count=max(prior, 1),
        )
        if blended is None:
            return
        profile.embedding = [float(value) for value in blended.tolist()]
        profile.embedding_samples = prior + 1 if prior > 0 else 1

    def _read_mono_audio(self, file_path: str) -> np.ndarray | None:
        """Load a file as mono float32 at the profile sample rate."""
        try:
            import soundfile as sf

            audio, sample_rate = sf.read(file_path, dtype="float32")
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            if sample_rate != self._sample_rate and len(audio) > 0:
                from scipy import signal

                count = int(len(audio) * self._sample_rate / sample_rate)
                audio = np.asarray(signal.resample(audio, count), dtype=np.float32)
            return np.asarray(audio, dtype=np.float32)
        except Exception as e:
            logger.warning("Could not read audio for profile match", error=str(e))
            return None

    def _link_clone_to_profile(
        self,
        audio: np.ndarray,
        voice_id: str,
        label: str,
        profile_id: str | None,
    ) -> VoiceProfile:
        """Create or update a profile from capture audio and assign *voice_id*."""
        profile: VoiceProfile | None = None
        if profile_id and profile_id in self._profiles:
            profile = self._profiles[profile_id]
        if profile is None:
            profile, _score = self._similar_profile(audio)
        if profile is None:
            for existing in self._profiles.values():
                if existing.name == label:
                    profile = existing
                    break

        if profile is None:
            profile = VoiceProfile(
                id=VoiceProfileStore.new_id(),
                name=label or f"Speaker {self._next_speaker_number()}",
            )
            emb = self._speaker_id.register_speaker(profile.id, audio)
            if emb is not None:
                profile.embedding = [float(value) for value in emb.tolist()]
                profile.embedding_samples = 1
        else:
            self._combine_profile_embedding(profile, audio)

        profile.assigned_voice_id = voice_id
        self._profiles[profile.id] = profile
        if self._default_profile_id is None:
            self._default_profile_id = profile.id
            try:
                self._profile_store.set_default_profile_id(profile.id)
            except Exception as e:
                logger.warning("Could not set default profile", error=str(e))
        try:
            self._profile_store.save(profile)
        except Exception as e:
            logger.warning("Could not persist linked profile", error=str(e))
        self._active_profile_id = profile.id
        return profile