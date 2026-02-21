"""
Voice cloning management for dynamic voice capture.

Supports multiple speaker voices, persistent caching, and manual
speaker selection.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import structlog

from live_dubbing.processing.speaker_id import SpeakerIdentifier
from live_dubbing.services.elevenlabs_service import ElevenLabsService

if TYPE_CHECKING:
    from live_dubbing.services.voice_store import VoiceStore

logger = structlog.get_logger(__name__)


@dataclass
class ClonedVoice:
    """Information about a cloned voice."""

    voice_id: str
    name: str
    created_at: datetime = field(default_factory=datetime.now)
    sample_duration_sec: float = 0.0
    is_dynamic: bool = False
    speaker_id: str | None = None


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
    ) -> None:
        """
        Initialize voice clone manager.

        Args:
            elevenlabs_service: ElevenLabs API service
            min_sample_duration_sec: Minimum audio duration for cloning
            max_sample_duration_sec: Maximum audio to capture for cloning
            voice_store: Optional persistent store for voice metadata
        """
        self._service = elevenlabs_service
        self._min_sample_duration = min_sample_duration_sec
        self._max_sample_duration = max_sample_duration_sec
        self._voice_store = voice_store

        # Cache of cloned voices (voice_id → ClonedVoice)
        self._voice_cache: dict[str, ClonedVoice] = {}

        # Speaker identification (MFCC-based)
        self._speaker_id = SpeakerIdentifier(sample_rate=16000)

        # Audio buffer for dynamic cloning
        self._audio_buffer: list[np.ndarray] = []
        self._buffer_duration_sec = 0.0
        self._is_capturing = False
        self._capture_speaker_label: str | None = None
        self._sample_rate = 16000
        self._buffer_lock = asyncio.Lock()

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

    async def start_dynamic_capture(
        self,
        sample_rate: int = 16000,
        speaker_label: str | None = None,
    ) -> None:
        """
        Start capturing audio for dynamic voice cloning.

        Args:
            sample_rate: Sample rate of incoming audio
            speaker_label: Label for the speaker being captured
        """
        self._audio_buffer = []
        self._buffer_duration_sec = 0.0
        self._is_capturing = True
        self._sample_rate = sample_rate
        self._capture_speaker_label = speaker_label

        logger.info(
            "Started dynamic voice capture",
            speaker_label=speaker_label,
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

            # Resolve speaker label
            label = speaker_label or self._capture_speaker_label

            # Clear buffer
            self._audio_buffer = []
            self._buffer_duration_sec = 0.0
            self._is_capturing = False
            self._capture_speaker_label = None

        # Convert to bytes (WAV format)
        audio_bytes = self._audio_to_wav(combined_audio)

        # Generate name if not provided
        if not name:
            name = label or f"dynamic_clone_{int(time.time())}"

        # Clone voice
        voice_id = await self._service.clone_voice(
            audio_data=audio_bytes,
            name=name,
            description=f"Cloned voice for speaker: {label or name}",
        )

        # Create voice info
        cloned_voice = ClonedVoice(
            voice_id=voice_id,
            name=name,
            sample_duration_sec=buffer_duration,
            is_dynamic=True,
            speaker_id=label,
        )

        # Cache the voice
        self._voice_cache[voice_id] = cloned_voice

        # Register speaker embedding for auto-detection
        self._speaker_id.register_speaker(voice_id, combined_audio)

        # Persist to store
        if self._voice_store:
            try:
                self._voice_store.save(cloned_voice)
            except Exception as e:
                logger.warning("Could not persist voice", error=str(e))

        logger.info(
            "Dynamic voice clone created",
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

        # Register speaker embedding from the audio file
        try:
            import soundfile as sf

            file_audio, sr = sf.read(file_path, dtype="float32")
            if file_audio.ndim > 1:
                file_audio = file_audio.mean(axis=1)
            if sr != self._sample_rate:
                from scipy import signal

                num = int(len(file_audio) * self._sample_rate / sr)
                file_audio = np.asarray(signal.resample(file_audio, num), dtype=np.float32)
            self._speaker_id.register_speaker(voice_id, file_audio)
        except Exception as e:
            logger.warning("Could not register speaker embedding from file", error=str(e))

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
            self._speaker_id.unregister_speaker(voice_id)

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
        logger.info("Dynamic capture cancelled")

    def identify_speaker(self, audio: np.ndarray) -> tuple[str | None, float]:
        """Identify which registered speaker is in *audio*.

        Returns:
            ``(voice_id, confidence)`` or ``(None, score)`` when below threshold.
        """
        return self._speaker_id.identify(audio)

    @property
    def can_identify_speakers(self) -> bool:
        """True when ≥2 speaker embeddings are registered."""
        return self._speaker_id.has_multiple_speakers
