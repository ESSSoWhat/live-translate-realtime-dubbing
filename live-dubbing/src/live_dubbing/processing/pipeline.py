"""
Async processing pipeline for real-time translation.
"""

import asyncio
import io
import re
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from live_dubbing.services.usage_reporter import UsageReporter

import numpy as np
import soundfile as sf
import structlog

from live_dubbing.core.events import EventBus, EventType
from live_dubbing.config.settings import redact_secrets
from live_dubbing.processing.text_filter import strip_non_verbal
from live_dubbing.processing.vad import SileroVAD
from live_dubbing.services.elevenlabs_service import ElevenLabsService
from live_dubbing.services.voice_cloning import ClonedVoice, VoiceCloneManager

logger = structlog.get_logger(__name__)


def _tts_error_message(exc: BaseException, short: bool = False) -> str:
    """Turn TTS API exception into a short user-facing message (no headers dump)."""
    s = str(exc)
    # Extract status code from exception or any wrapped cause
    status_code: int | None = None
    to_check: list[BaseException] = [exc]
    if exc.__cause__:
        to_check.append(exc.__cause__)
    if exc.__context__:
        to_check.append(exc.__context__)
    for e in to_check:
        try:
            resp = getattr(e, "response", None)
            if resp is not None:
                status_code = getattr(resp, "status_code", None)
                if status_code is not None:
                    break
        except Exception:
            pass
        status_code = getattr(e, "status_code", None)
        if status_code is not None:
            break
    # Fallback: parse "401", "status code 401", "402 Unauthorized", etc. from message
    if status_code is None:
        for match in re.finditer(r"(?:status\s*code\s*|HTTP\s+)(\d{3})|\b(401|402|429|500)\b", s, re.I):
            status_code = int(match.group(1) or match.group(2))
            break
    if status_code is not None:
        msg = {
            401: "Invalid API key or not signed in",
            402: "Quota or payment required",
            429: "Rate limit exceeded",
            500: "Server error — try again later",
        }.get(status_code)
        if msg:
            return msg if short else f"TTS failed: {msg}"
    # Avoid dumping raw response (e.g. "headers: {...}")
    if "headers:" in s or "headers':" in s:
        return "TTS API error (check API key and quota)" if short else "TTS failed: API error. Check API key and quota."
    # Keep first line only, cap length
    first = s.split("\n")[0].strip()
    if len(first) > 60:
        first = first[:57] + "..."
    return first if short else f"TTS failed: {first}"


def _float32_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert float32 mono to 16-bit PCM WAV for API compatibility."""
    buf = io.BytesIO()
    # ElevenLabs and most APIs expect 16-bit PCM WAV
    int16 = (np.clip(audio.astype(np.float64) * 32767.0, -32768, 32767)).astype(
        np.int16
    )
    sf.write(buf, int16, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


def _is_mp3_like(data: bytes) -> bool:
    """Heuristic: ID3 tag or MP3 frame sync."""
    if len(data) < 3:
        return False
    if data[:3] == b"ID3":
        return True
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _tts_audio_to_float32_bytes(audio_bytes: bytes) -> bytes:
    """Convert TTS output (MP3 or PCM) to float32 bytes for playback at 24kHz."""
    _target_sr = 24000
    if not audio_bytes:
        return b""

    # Prefer pydub for MP3 (dubbing API returns MP3); needs ffmpeg
    if _is_mp3_like(audio_bytes):
        try:
            from pydub import AudioSegment
            from scipy import signal
            seg = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
            samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
            sr = seg.frame_rate
            if sr != _target_sr:
                num = int(len(samples) * _target_sr / sr)
                samples = signal.resample(samples, num).astype(np.float32)
            out = samples.tobytes()
            if len(out) > 0:
                return out
        except Exception as e:
            logger.warning(
                "MP3 decode failed (install ffmpeg for pydub). No playback.",
                error=str(e),
            )
            return b""

    try:
        data, sr = sf.read(io.BytesIO(audio_bytes))
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        if sr != _target_sr:
            from scipy import signal
            num = int(len(data) * _target_sr / sr)
            data = signal.resample(data, num).astype(np.float32)
        return cast(bytes, data.tobytes())
    except Exception:
        pass
    try:
        from pydub import AudioSegment
        from scipy import signal
        seg = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
        sr = seg.frame_rate
        if sr != _target_sr:
            num = int(len(samples) * _target_sr / sr)
            samples = signal.resample(samples, num).astype(np.float32)
        return samples.tobytes()
    except Exception:
        pass
    try:
        arr = np.frombuffer(audio_bytes, dtype=np.int16)
        return (arr.astype(np.float32) / 32768.0).tobytes()
    except Exception:
        pass
    logger.warning("Could not convert TTS audio to float32; skipping playback")
    return b""


class PipelineState(Enum):
    """Pipeline state."""

    IDLE = auto()
    CAPTURING_VOICE = auto()
    PROCESSING = auto()
    PAUSED = auto()
    ERROR = auto()


@dataclass
class PipelineConfig:
    """Configuration for processing pipeline."""

    target_language: str = "en"
    source_language: str = "auto"
    voice_stability: float = 0.5
    voice_similarity: float = 0.75
    min_voice_capture_sec: float = 30.0
    use_premade_voice_id: str | None = None  # Skip cloning; use this voice
    use_dubbing_api: bool = False  # Dubbing API is batch (~30s latency); use STT+translate+TTS for real-time
    auto_clone_voice: bool = True  # Auto-clone speaker voice on start
    usage_reporter: "UsageReporter | None" = None  # Report usage when using direct ElevenLabs


@dataclass
class AudioChunk:
    """Audio chunk with metadata."""

    data: np.ndarray
    timestamp_ms: int
    sample_rate: int = 16000
    is_speech: bool = False
    vad_confidence: float = 0.0


@dataclass
class ProcessedChunk:
    """Processed output chunk."""

    audio_data: bytes
    original_text: str
    translated_text: str
    timestamp_ms: int
    latency_ms: float


@dataclass
class PipelineStats:
    """Pipeline performance statistics."""

    chunks_processed: int = 0
    total_audio_sec: float = 0.0
    total_speech_sec: float = 0.0
    average_latency_ms: float = 0.0
    current_latency_ms: float = 0.0
    voice_clone_ready: bool = False
    last_transcription: str = ""
    last_translation: str = ""


class ProcessingPipeline:
    """
    Real-time audio processing pipeline.

    Stages:
    1. VAD - Filter speech from silence
    2. Voice Capture - Capture audio for voice cloning
    3. STT - Transcribe speech to text
    4. TTS - Synthesize translated speech

    Uses async queues for stage-to-stage communication.
    """

    def __init__(
        self,
        elevenlabs_service: ElevenLabsService | None,
        event_bus: EventBus,
        config: PipelineConfig | None = None,
    ) -> None:
        """
        Initialize processing pipeline.

        Args:
            elevenlabs_service: ElevenLabs API service
            event_bus: Event bus for notifications
            config: Pipeline configuration
        """
        self._service = elevenlabs_service
        self._event_bus = event_bus
        self._config = config or PipelineConfig()

        # Initialize components (lower threshold = more sensitive, captures quieter speech)
        self._vad = SileroVAD(threshold=0.05, min_silence_duration_ms=250)
        self._voice_manager: VoiceCloneManager | None = None
        if elevenlabs_service:
            # Create persistent voice store
            from live_dubbing.services.voice_store import VoiceStore
            voice_store = VoiceStore()
            self._voice_manager = VoiceCloneManager(
                elevenlabs_service,
                min_sample_duration_sec=self._config.min_voice_capture_sec,
                voice_store=voice_store,
            )

        # State
        self._state = PipelineState.IDLE
        self._cloned_voice: ClonedVoice | None = None
        self._stats = PipelineStats()
        self._voice_capture_start_time: float = 0.0
        self._is_cloning_in_background = False

        # Async queues (larger to reduce drops when pipeline is briefly slow)
        self._vad_queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=100)
        self._stt_queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=30)
        self._tts_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=20)
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        # Tasks
        self._tasks: list[asyncio.Task] = []

        # Dedicated thread pool for VAD (single thread to avoid PyTorch threading issues)
        self._vad_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad")

        # Callbacks
        self._on_output: Callable[[bytes], Awaitable[None]] | None = None
        self._on_transcription: Callable[[str], Awaitable[None]] | None = None

        # Speech buffer for STT
        self._speech_buffer: list[np.ndarray] = []
        self._speech_buffer_duration_sec = 0.0
        # Min ~1s avoids fragmented transcriptions; STT quality drops on very short clips (<0.8s)
        self._min_stt_duration_sec = 1.0
        self._max_speech_buffer_sec = 8.0  # Flush to STT after this even without silence
        self._processing_start_time: float = 0.0
        self._last_stt_flush_time: float = 0.0

        # Silence tracking — bridge short pauses so we capture full sentences
        self._silence_count = 0  # consecutive silence chunks
        self._silence_flush_threshold = 6  # flush after ~600ms silence; avoids splitting mid-sentence pauses

        # Output suppression — prevent feedback loop when dubbed audio plays back
        # through the same device being captured (system loopback mode)
        self._output_playing = False
        self._output_suppress_until: float = 0.0

        # Throttle AUDIO_LEVEL_UPDATE to avoid flooding logs/UI when level is constant
        self._last_audio_level_emit_time: float = 0.0
        self._last_audio_level: float | None = None
        self._last_audio_is_speech: bool | None = None

    async def start(
        self,
        on_output: Callable[[bytes], Awaitable[None]] | None = None,
        on_transcription: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """
        Start the processing pipeline.

        Args:
            on_output: Callback for output audio
            on_transcription: Callback for transcription text
        """
        if self._state != PipelineState.IDLE:
            logger.warning("Pipeline already running")
            return

        logger.info("Starting processing pipeline")

        self._on_output = on_output
        self._on_transcription = on_transcription

        # Reset one-time skip flag so we can warn again this run if TTS is skipped
        if hasattr(self, "_tts_skip_logged"):
            del self._tts_skip_logged

        # Load VAD model synchronously (fast enough that it won't block significantly)
        self._vad.load_model()

        if not self._service:
            self._event_bus.emit_warning(
                "ElevenLabs API key not set. STT, TTS and voice clone will not work. "
                "Set ELEVENLABS_API_KEY in .env or sign in to use the service.",
                {},
            )

        # Default fallback voice ID (ElevenLabs "Rachel")
        _FALLBACK_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

        # Always start in PROCESSING state so STT/translate/TTS work immediately.
        # Use a premade voice for TTS right away; swap to cloned voice when ready.
        premade_id = (self._config.use_premade_voice_id or "").strip()
        initial_voice_id = premade_id or _FALLBACK_VOICE_ID
        self._cloned_voice = ClonedVoice(
            voice_id=initial_voice_id,
            name="Premade (temporary)" if not premade_id else "Premade",
            is_dynamic=False,
        )
        self._state = PipelineState.PROCESSING
        self._processing_start_time = time.time()
        self._last_stt_flush_time = time.time()

        if premade_id:
            # User explicitly chose a premade voice — no cloning needed
            self._stats.voice_clone_ready = True
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {"voice_id": self._cloned_voice.voice_id},
            )
            logger.info("Using premade voice, skipping clone", voice_id=premade_id)
        elif self._voice_manager and self._config.auto_clone_voice:
            # Start voice capture for dynamic cloning IN PARALLEL with processing.
            # TTS uses the fallback voice until clone is ready, then swaps.
            self._voice_capture_start_time = time.time()
            self._is_cloning_in_background = True
            await self._voice_manager.start_dynamic_capture()
            # Emit COMPLETED immediately so orchestrator transitions to TRANSLATING state
            # (the fallback voice is usable right now; clone will upgrade it later)
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {"voice_id": self._cloned_voice.voice_id, "is_temporary": True},
            )
            self._event_bus.emit(EventType.VOICE_CLONE_STARTED, {})
            logger.info(
                "Started voice capture in background, using fallback voice for now",
                fallback_voice=initial_voice_id,
            )
        else:
            self._stats.voice_clone_ready = True

        # Start processing tasks
        self._tasks = [
            asyncio.create_task(self._vad_stage()),
            asyncio.create_task(self._stt_stage()),
            asyncio.create_task(self._tts_stage()),
            asyncio.create_task(self._output_stage()),
        ]

        # Yield to event loop so tasks can start running before we return
        await asyncio.sleep(0)

        logger.info("Pipeline started", state=self._state.name)

    async def stop(self) -> None:
        """Stop the processing pipeline."""
        if self._state == PipelineState.IDLE:
            return

        logger.info("Stopping processing pipeline")

        self._state = PipelineState.IDLE

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

        # Clear queues
        self._clear_queues()

        # Reset state
        self._speech_buffer = []
        self._speech_buffer_duration_sec = 0.0
        self._output_suppress_until = 0.0
        self._output_playing = False
        self._is_cloning_in_background = False
        self._vad.reset_state()

        # Shutdown VAD executor (don't wait for pending tasks)
        self._vad_executor.shutdown(wait=False)
        # Recreate executor for next start
        self._vad_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad")

        logger.info("Pipeline stopped")

    def _clear_queues(self) -> None:
        """Clear all queues."""
        queues: list[asyncio.Queue[AudioChunk] | asyncio.Queue[str] | asyncio.Queue[bytes]] = [
            self._vad_queue, self._stt_queue, self._tts_queue, self._output_queue,
        ]
        for q in queues:
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

    def get_queue_depths(self) -> dict[str, int]:
        """
        Get current queue depths for debugging.

        Returns:
            Dict with queue names and their current sizes
        """
        return {
            "vad": self._vad_queue.qsize(),
            "stt": self._stt_queue.qsize(),
            "tts": self._tts_queue.qsize(),
            "output": self._output_queue.qsize(),
        }

    async def _translate_for_tts(self, text: str, source_language: str) -> str:
        """Translate text to target language for TTS. Passthrough if same or no translator."""
        if not text.strip():
            return text
        target = (self._config.target_language or "en").strip().lower()
        if target == "auto":
            logger.debug("Translation skipped - target is 'auto'")
            return text
        if target == source_language:
            logger.debug("No translation: target equals source", target=target)
            return text
        if self._service and hasattr(self._service, "translate_text"):
            try:
                logger.info(
                    "Translating for TTS",
                    source=source_language,
                    target=target,
                    len=len(text),
                )
                return await self._service.translate_text(text, target)
            except Exception as e:
                logger.warning("Translation failed, using original", error=str(e))
                return text
        logger.warning("Translation skipped - no translate_text on service")
        return text

    def _flush_speech_buffer(self, timestamp_ms: int, reason: str) -> None:
        """Flush accumulated speech buffer to the STT queue."""
        if not self._speech_buffer:
            return

        combined = np.concatenate(self._speech_buffer)

        # Speaker identification: switch TTS voice if a different speaker is detected
        self._detect_and_switch_speaker(combined)

        stt_chunk = AudioChunk(
            data=combined,
            timestamp_ms=timestamp_ms,
            is_speech=True,
        )
        try:
            self._stt_queue.put_nowait(stt_chunk)
            self._last_stt_flush_time = time.time()
            logger.info(
                "Sent speech to STT",
                reason=reason,
                duration_sec=round(self._speech_buffer_duration_sec, 2),
                samples=len(combined),
            )
        except asyncio.QueueFull:
            logger.warning("STT queue full, dropping chunk")

        self._stats.total_speech_sec += self._speech_buffer_duration_sec
        self._speech_buffer = []
        self._speech_buffer_duration_sec = 0.0
        self._silence_count = 0

    def _detect_and_switch_speaker(self, audio: np.ndarray) -> None:
        """Identify the current speaker and switch TTS voice if needed."""
        if not self._voice_manager or not self._voice_manager.can_identify_speakers:
            return
        if self._voice_manager.is_capturing:
            return  # Don't switch while actively capturing a new voice

        voice_id, confidence = self._voice_manager.identify_speaker(audio)
        if voice_id is None:
            return
        if self._cloned_voice and voice_id == self._cloned_voice.voice_id:
            return  # Already using this voice

        # Switch to the identified speaker's cloned voice
        matched_voice = self._voice_manager.get_cached_voice(voice_id)
        if matched_voice:
            self._cloned_voice = matched_voice
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {
                    "voice_id": matched_voice.voice_id,
                    "name": matched_voice.name,
                    "auto_switched": True,
                },
            )
            logger.info(
                "Auto-switched TTS voice to detected speaker",
                voice_id=voice_id,
                name=matched_voice.name,
                confidence=f"{confidence:.2f}",
            )

    async def process_chunk(
        self,
        audio_data: bytes,
        timestamp_ms: int,
    ) -> None:
        """
        Process an incoming audio chunk.

        Args:
            audio_data: Raw audio bytes (float32)
            timestamp_ms: Timestamp in milliseconds
        """
        # Debug: log pipeline receiving chunks
        if self._stats.chunks_processed % 100 == 0:
            logger.info(
                "Pipeline process_chunk",
                state=self._state.name,
                chunks_so_far=self._stats.chunks_processed,
                queue_size=self._vad_queue.qsize(),
            )

        if self._state == PipelineState.IDLE:
            return

        # Suppress audio captured during our own TTS playback to prevent feedback
        if time.time() < self._output_suppress_until:
            self._stats.chunks_processed += 1
            return

        # Convert bytes to numpy array
        audio = np.frombuffer(audio_data, dtype=np.float32)

        # Create chunk
        chunk = AudioChunk(
            data=audio,
            timestamp_ms=timestamp_ms,
            sample_rate=16000,
        )

        # Add to VAD queue
        try:
            self._vad_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # Drop oldest if full
            try:
                self._vad_queue.get_nowait()
                self._vad_queue.put_nowait(chunk)
            except asyncio.QueueEmpty:
                pass

        # Update stats
        self._stats.chunks_processed += 1
        self._stats.total_audio_sec += len(audio) / 16000

    async def _vad_stage(self) -> None:
        """VAD stage - filter speech from silence."""
        logger.info("VAD stage started")
        vad_chunks_processed = 0

        while self._state != PipelineState.IDLE:
            try:
                # Get chunk with timeout
                try:
                    chunk = await asyncio.wait_for(
                        self._vad_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                vad_chunks_processed += 1
                if vad_chunks_processed <= 5 or vad_chunks_processed % 50 == 0:
                    logger.info(
                        "VAD processing chunk",
                        vad_chunk=vad_chunks_processed,
                        queue_size=self._vad_queue.qsize(),
                    )

                # Run VAD synchronously - PyTorch models have threading issues with executors
                # The VAD inference is fast enough (<1ms) that it won't block the event loop significantly
                logger.debug("VAD about to process", vad_chunk=vad_chunks_processed)
                try:
                    vad_result = self._vad.process_chunk(chunk.data, chunk.timestamp_ms)
                except Exception as e:
                    logger.error("VAD process_chunk error", error=str(e), vad_chunk=vad_chunks_processed)
                    continue
                logger.debug("VAD processed", vad_chunk=vad_chunks_processed)
                chunk.is_speech = vad_result.is_speech
                chunk.vad_confidence = vad_result.confidence

                # Debug: Log VAD results periodically (use info level for visibility)
                if vad_chunks_processed % 50 == 0:
                    level = np.sqrt(np.mean(chunk.data ** 2))
                    logger.info(
                        "VAD status",
                        is_speech=vad_result.is_speech,
                        confidence=f"{vad_result.confidence:.3f}",
                        audio_level=f"{level:.4f}",
                        vad_chunks=vad_chunks_processed,
                        state=self._state.name,
                    )

                # Voice cloning: feed speech to cloner (both auto background and manual captures)
                if self._voice_manager and self._voice_manager.is_capturing and vad_result.is_speech:
                    ready = self._voice_manager.add_audio_chunk(chunk.data)
                    self._event_bus.emit(
                        EventType.VOICE_CLONE_PROGRESS,
                        {
                            "progress": self._voice_manager.capture_progress,
                            "speaker_label": self._voice_manager.capture_speaker_label or "",
                        },
                    )
                    if ready:
                        if self._is_cloning_in_background:
                            # Initial auto-clone: swap active voice from fallback
                            self._is_cloning_in_background = False
                            asyncio.create_task(self._run_voice_clone())
                        else:
                            # Manual capture: clone and add to cache without auto-activating
                            asyncio.create_task(self._run_manual_voice_clone())

                # Always process speech for STT/translate/TTS (even during cloning)
                if self._state == PipelineState.PROCESSING:
                    if vad_result.is_speech:
                        # Speech detected — accumulate and reset silence counter
                        self._speech_buffer.append(chunk.data)
                        self._speech_buffer_duration_sec += len(chunk.data) / 16000
                        self._silence_count = 0

                        # Safety: flush very long continuous speech so we don't hold forever
                        if self._speech_buffer and self._speech_buffer_duration_sec >= self._max_speech_buffer_sec:
                            self._flush_speech_buffer(chunk.timestamp_ms, "max buffer")

                    else:
                        # Silence detected
                        if self._speech_buffer:
                            # Include silence audio in buffer to preserve natural gaps
                            self._speech_buffer.append(chunk.data)
                            self._speech_buffer_duration_sec += len(chunk.data) / 16000
                            self._silence_count += 1

                            # Flush when we see a sustained silence gap (speaker finished a phrase)
                            if (
                                self._silence_count >= self._silence_flush_threshold
                                and self._speech_buffer_duration_sec >= self._min_stt_duration_sec
                            ):
                                self._flush_speech_buffer(chunk.timestamp_ms, "silence")
                        else:
                            # No buffered speech — nothing to do
                            self._silence_count = 0

                # Emit audio level (throttled: only when level/speech changes or every 200ms)
                level = np.sqrt(np.mean(chunk.data ** 2))
                level_f = float(level)
                now = time.time()
                last_level = self._last_audio_level
                last_speech = self._last_audio_is_speech
                level_changed = last_level is None or abs(level_f - last_level) > 0.03
                speech_changed = last_speech is not None and last_speech != vad_result.is_speech
                interval_elapsed = (now - self._last_audio_level_emit_time) >= 0.2
                if level_changed or speech_changed or interval_elapsed:
                    self._last_audio_level_emit_time = now
                    self._last_audio_level = level_f
                    self._last_audio_is_speech = vad_result.is_speech
                    self._event_bus.emit(
                        EventType.AUDIO_LEVEL_UPDATE,
                        {"level": level_f, "is_speech": vad_result.is_speech},
                    )

                # Periodic log so we know VAD stage is still running in PROCESSING
                if self._state == PipelineState.PROCESSING and self._stats.chunks_processed % 100 == 0:
                    logger.info(
                        "VAD PROCESSING",
                        chunks=self._stats.chunks_processed,
                        buffer_sec=round(self._speech_buffer_duration_sec, 2),
                        is_speech=vad_result.is_speech,
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("VAD stage error", error=str(e))

        logger.info("VAD stage stopped")

    async def _run_voice_clone(self) -> None:
        """Run voice cloning in the background without blocking VAD."""
        try:
            if not self._voice_manager:
                return
            cloned = await self._voice_manager.create_dynamic_clone()
            self._cloned_voice = cloned
            self._stats.voice_clone_ready = True
            if self._config.usage_reporter:
                self._config.usage_reporter.report("clone", 1)
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {"voice_id": cloned.voice_id},
            )
            logger.info(
                "Voice clone ready, swapped from fallback",
                voice_id=cloned.voice_id,
            )
        except Exception as e:
            logger.exception("Voice clone failed, keeping fallback", error=str(e))
            err_s = redact_secrets(str(e))
            self._event_bus.emit(
                EventType.VOICE_CLONE_FAILED,
                {"error": err_s},
            )
            hint = ""
            if "getaddrinfo" in err_s or "connection" in err_s.lower():
                hint = " Check that the backend URL is reachable (or clear LIVE_TRANSLATE_BACKEND_URL to use production)."
            elif "401" in err_s or "session" in err_s.lower() or "expired" in err_s.lower():
                hint = " Sign in again from Account → Sign in."
            elif "402" in err_s or "quota" in err_s.lower():
                hint = " Voice clone limit reached. Upgrade your plan for more."
            self._event_bus.emit_warning(
                "Voice clone failed — using default voice for TTS. You can still hear translated speech."
                + hint,
                {"error": err_s[:100]},
            )

    async def _run_manual_voice_clone(self) -> None:
        """Run voice cloning for a manually captured voice (does not auto-activate)."""
        try:
            if not self._voice_manager:
                return
            cloned = await self._voice_manager.create_dynamic_clone()
            if self._config.usage_reporter:
                self._config.usage_reporter.report("clone", 1)
            # Don't swap active voice — let the user activate it from the list
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {"voice_id": cloned.voice_id, "name": cloned.name},
            )
            logger.info(
                "Manual voice clone created",
                voice_id=cloned.voice_id,
                name=cloned.name,
            )
        except Exception as e:
            logger.exception("Manual voice clone failed", error=str(e))
            self._event_bus.emit(
                EventType.VOICE_CLONE_FAILED,
                {"error": redact_secrets(str(e))},
            )

    async def _stt_stage(self) -> None:
        """STT stage - transcribe speech to text (or use dubbing API for full pipeline)."""
        logger.info("STT stage started", use_dubbing_api=self._config.use_dubbing_api)

        while self._state != PipelineState.IDLE:
            try:
                # Get chunk
                try:
                    chunk = await asyncio.wait_for(
                        self._stt_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                # Reset from config each chunk so we retry dubbing after transient failures
                use_dubbing = self._config.use_dubbing_api

                if not self._service:
                    if not getattr(self, "_stt_skip_logged", False):
                        self._stt_skip_logged = True
                        logger.warning("STT stage: no ElevenLabs service, skipping")
                        self._event_bus.emit_warning(
                            "Speech-to-text disabled: sign in or set ELEVENLABS_API_KEY.",
                            {},
                        )
                    continue

                # Convert to WAV for API (ElevenLabs expects file format)
                audio_wav = _float32_to_wav_bytes(chunk.data, 16000)
                logger.info(
                    "STT processing chunk",
                    samples=len(chunk.data),
                    duration_sec=round(len(chunk.data) / 16000, 2),
                    use_dubbing=use_dubbing,
                )

                start_time = time.time()

                # Use ElevenLabs Dubbing API (handles STT + translate + TTS in one call)
                if use_dubbing:
                    try:
                        target_lang = self._config.target_language or "en"
                        source_lang = self._config.source_language or "auto"

                        # Check if translation is needed
                        if target_lang == source_lang or target_lang == "auto":
                            logger.debug("Dubbing skipped - same language or auto target")
                            # Fall back to STT-only path
                            use_dubbing = False
                        else:
                            logger.info(
                                "Using ElevenLabs Dubbing API",
                                source=source_lang,
                                target=target_lang,
                            )

                            dub_result = await self._service.dub_audio(
                                audio_data=audio_wav,
                                source_language=source_lang,
                                target_language=target_lang,
                                poll_interval=0.5,
                                max_wait_seconds=60.0,
                            )

                            if dub_result:
                                # Dubbed audio is ready - send directly to output
                                latency_ms = (time.time() - start_time) * 1000
                                self._stats.current_latency_ms = latency_ms

                                # Show source transcription in transcription window
                                source_text = strip_non_verbal(
                                    dub_result.source_text or ""
                                )
                                if source_text:
                                    self._stats.last_transcription = source_text
                                    self._event_bus.emit(
                                        EventType.TRANSCRIPTION_UPDATE,
                                        {"text": source_text, "language": source_lang},
                                    )

                                # Show translated text in the translation display
                                translated_text = strip_non_verbal(
                                    dub_result.translated_text or ""
                                ) or "[Dubbed]"
                                self._stats.last_translation = translated_text
                                self._event_bus.emit(
                                    EventType.TRANSLATION_UPDATE,
                                    {"text": translated_text},
                                )

                                # Convert dubbed audio (MP3) to playable format
                                dubbed_float32 = _tts_audio_to_float32_bytes(dub_result.audio)

                                if not dubbed_float32:
                                    logger.warning(
                                        "Dubbed audio conversion produced no data "
                                        "(install ffmpeg for MP3 playback)"
                                    )
                                    self._event_bus.emit_warning(
                                        "Audio conversion failed. Install ffmpeg for playback.",
                                        {},
                                    )
                                    continue

                                # Queue for output
                                try:
                                    self._output_queue.put_nowait(dubbed_float32)
                                    logger.info(
                                        "Dubbed audio queued for output",
                                        size=len(dubbed_float32),
                                        latency_ms=round(latency_ms, 1),
                                    )
                                    self._event_bus.emit(
                                        EventType.TTS_COMPLETED,
                                        {"text": translated_text, "audio_size": len(dubbed_float32)},
                                    )
                                except asyncio.QueueFull:
                                    logger.warning("Output queue full, dropping dubbed audio")

                                continue  # Skip the rest, dubbing handled everything
                            else:
                                logger.warning("Dubbing failed, falling back to STT+translate+TTS")
                                use_dubbing = False  # Fall back for this chunk

                    except Exception as e:
                        logger.exception("Dubbing API error, falling back", error=str(e))
                        use_dubbing = False

                # Standard path: STT → translate → TTS
                try:
                    result = await self._service.transcribe(
                        audio_data=audio_wav,
                        language=self._config.source_language,
                    )

                    # Report STT usage when using direct API
                    if self._config.usage_reporter and audio_wav:
                        stt_sec = max(1, len(audio_wav) // 32000)  # 16-bit mono 16kHz
                        self._config.usage_reporter.report("stt", stt_sec)

                    transcription = (result.text or "").strip()
                    self._stats.last_transcription = transcription

                    # Emit transcription (always non-empty so UI updates)
                    display_text = transcription if transcription else "[No speech detected]"
                    self._event_bus.emit(
                        EventType.TRANSCRIPTION_UPDATE,
                        {"text": display_text, "language": result.language_code},
                    )

                    if self._on_transcription:
                        await self._on_transcription(transcription)

                    # Translate to target language (passthrough if same or no translator)
                    text_for_tts = await self._translate_for_tts(
                        transcription, result.language_code
                    )
                    # Report translation usage when using direct API (if we translated)
                    if self._config.usage_reporter and text_for_tts and text_for_tts != transcription:
                        self._config.usage_reporter.report("translate", max(1, len(text_for_tts)))

                    # Emit translation result so UI shows it immediately
                    if text_for_tts.strip() and text_for_tts != transcription:
                        self._stats.last_translation = text_for_tts
                        self._event_bus.emit(
                            EventType.TRANSLATION_UPDATE,
                            {"text": text_for_tts},
                        )

                    # Strip non-verbal markers before TTS
                    text_for_tts = strip_non_verbal(text_for_tts)

                    # Send to TTS
                    if text_for_tts.strip():
                        try:
                            self._tts_queue.put_nowait(text_for_tts)
                            logger.info("Queued for TTS", text_len=len(text_for_tts))
                        except asyncio.QueueFull:
                            logger.warning("TTS queue full")

                    # Update latency
                    latency_ms = (time.time() - start_time) * 1000
                    self._stats.current_latency_ms = latency_ms

                except Exception as e:
                    from live_dubbing.services.backend_service import AuthExpiredException
                    logger.exception("STT failed", error=str(e))
                    err_msg = str(e)[:80] if str(e) else "Unknown error"
                    self._event_bus.emit(
                        EventType.TRANSCRIPTION_UPDATE,
                        {"text": f"[Transcription failed: {err_msg}]"},
                    )
                    if isinstance(e, AuthExpiredException):
                        self._event_bus.emit(EventType.AUTH_EXPIRED, {"message": str(e)})

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("STT stage error", error=str(e))

        logger.info("STT stage stopped")

    async def _tts_stage(self) -> None:
        """TTS stage - synthesize speech with cloned voice."""
        logger.info("TTS stage started")

        while self._state != PipelineState.IDLE:
            try:
                # Get text
                try:
                    text = await asyncio.wait_for(
                        self._tts_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                if not self._service or not self._cloned_voice:
                    if not hasattr(self, "_tts_skip_logged"):
                        self._tts_skip_logged = True
                        logger.warning(
                            "TTS stage: no service or cloned voice, skipping",
                            has_service=self._service is not None,
                            has_cloned_voice=self._cloned_voice is not None,
                        )
                        self._event_bus.emit_warning(
                            "TTS is not available — no API key or voice configured. "
                            "Transcription and translation will work but no speech output.",
                            {"has_service": self._service is not None, "has_voice": self._cloned_voice is not None},
                        )
                    continue

                # Synthesize with cloned voice
                try:
                    self._event_bus.emit(EventType.TTS_STARTED, {"text": text})

                    audio_bytes = await self._service.synthesize(
                        text=text,
                        voice_id=self._cloned_voice.voice_id,
                        stability=self._config.voice_stability,
                        similarity_boost=self._config.voice_similarity,
                    )

                    # Report TTS and dub usage when using direct API
                    if self._config.usage_reporter:
                        self._config.usage_reporter.report("tts", max(1, len(text)))
                        dub_sec = max(1, len(audio_bytes) // 16000)  # MP3 ~128kbps rough
                        self._config.usage_reporter.report("dub", dub_sec)

                    # Convert to float32 for playback (TTS returns MP3)
                    playback_bytes = _tts_audio_to_float32_bytes(audio_bytes)

                    if not playback_bytes:
                        logger.warning("TTS conversion produced no audio (check ffmpeg)")
                        self._event_bus.emit_warning(
                            "TTS audio conversion failed. Ensure ffmpeg is on PATH.",
                            {},
                        )
                    else:
                        try:
                            self._output_queue.put_nowait(playback_bytes)
                        except asyncio.QueueFull:
                            logger.warning("Output queue full")
                    self._event_bus.emit(EventType.TTS_COMPLETED, {})

                except Exception as e:
                    from live_dubbing.services.backend_service import AuthExpiredException
                    logger.exception("TTS failed", error=str(e))
                    user_msg = _tts_error_message(e)
                    self._event_bus.emit_warning(
                        user_msg,
                        {"error": redact_secrets(str(e))},
                    )
                    if isinstance(e, AuthExpiredException):
                        self._event_bus.emit(EventType.AUTH_EXPIRED, {"message": str(e)})
                    self._event_bus.emit(
                        EventType.TRANSLATION_UPDATE,
                        {"text": f"[TTS failed: {short_msg}]"},
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("TTS stage error", error=str(e))

        logger.info("TTS stage stopped")

    async def _output_stage(self) -> None:
        """Output stage - deliver audio to callback."""
        logger.info("Output stage started")

        while self._state != PipelineState.IDLE:
            try:
                # Get audio
                try:
                    audio_bytes = await asyncio.wait_for(
                        self._output_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                # Calculate playback duration and suppress capture during playback
                # to prevent feedback loop (app must never capture its own audio)
                # float32 at 24kHz = 4 bytes per sample
                playback_duration_sec = len(audio_bytes) / (4 * 24000)
                # Buffer for playback queue + device latency + loopback delay (~0.5s)
                self._output_suppress_until = time.time() + playback_duration_sec + 0.5

                # Deliver to callback
                if self._on_output:
                    await self._on_output(audio_bytes)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Output stage error", error=str(e))

        logger.info("Output stage stopped")

    def set_config(self, config: PipelineConfig) -> None:
        """Update pipeline configuration."""
        self._config = config

    @property
    def state(self) -> PipelineState:
        """Get current pipeline state."""
        return self._state

    @property
    def stats(self) -> PipelineStats:
        """Get pipeline statistics."""
        return self._stats

    @property
    def is_voice_ready(self) -> bool:
        """Check if voice clone is ready."""
        return self._cloned_voice is not None

    @property
    def voice_capture_progress(self) -> float:
        """Get voice capture progress (0.0 to 1.0)."""
        if self._voice_manager:
            return self._voice_manager.capture_progress
        return 0.0

    def set_active_voice(self, voice: ClonedVoice) -> None:
        """Switch the active TTS voice at runtime.

        Args:
            voice: The cloned voice to use for subsequent TTS calls.
        """
        self._cloned_voice = voice
        logger.info(
            "Active voice changed",
            voice_id=voice.voice_id,
            name=voice.name,
        )
