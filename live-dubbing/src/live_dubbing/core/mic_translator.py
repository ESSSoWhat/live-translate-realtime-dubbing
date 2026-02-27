"""
Mic output translation session manager.

Captures the user's real microphone, runs the audio through the STT →
translate → TTS pipeline, then plays the result through a virtual output
device (VB-Cable Input) so other apps hear the translated voice.

Data-flow::

    Real Mic ─► MicCapture ─► ProcessingPipeline ─► AudioPlayback(CABLE Input)
                                                           └─► Discord / Zoom / etc.

Design notes:
  - ``_MicPipelineBus`` is an EventBus subclass that intercepts pipeline events
    and republishes them on the *main* bus under mic-specific event types so
    they don't pollute the main dubbing UI.
  - ``MicTranslator`` creates fresh instances of all three components on every
    ``start()`` call and tears them down on ``stop()``.
  - ``start()`` accepts the ``elevenlabs_service`` instance at call time (not in
    the constructor) so it always uses the currently active service instance.
"""

from __future__ import annotations

from typing import Any

import structlog

from live_dubbing.config.settings import AppSettings
from live_dubbing.core.events import EventBus, EventType

logger = structlog.get_logger(__name__)


# ── Mic pipeline event bus ─────────────────────────────────────────────────


class _MicPipelineBus(EventBus):
    """
    Isolated EventBus for the mic translation pipeline.

    Intercepts events emitted by ``ProcessingPipeline`` and selectively
    forwards them to the main application bus under renamed event types, so
    the main dubbing UI (transcription display, state labels, etc.) is not
    affected by mic pipeline activity.

    Forwarding rules:
    - TRANSCRIPTION_UPDATE  → main bus as MIC_TRANSCRIPTION_UPDATE
    - TRANSLATION_UPDATE    → main bus as MIC_TRANSLATION_UPDATE
    - AUDIO_LEVEL_UPDATE    → main bus as AUDIO_LEVEL_UPDATE with source="mic"
    - ERROR_OCCURRED        → forwarded to main bus unchanged
    - WARNING_OCCURRED      → forwarded to main bus unchanged
    - Everything else       → local only (not forwarded to main bus)
    """

    def __init__(self, main_bus: EventBus) -> None:
        super().__init__()
        self._main_bus = main_bus

    def emit(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:  # type: ignore[override]
        # Dispatch locally first so any pipeline-internal subscribers still work
        super().emit(event_type, data)

        d = data or {}

        # Forward selected events to main bus under appropriate types
        if event_type == EventType.TRANSCRIPTION_UPDATE:
            self._main_bus.emit(EventType.MIC_TRANSCRIPTION_UPDATE, d)
        elif event_type == EventType.TRANSLATION_UPDATE:
            self._main_bus.emit(EventType.MIC_TRANSLATION_UPDATE, d)
        elif event_type == EventType.AUDIO_LEVEL_UPDATE:
            # Tag with source="mic" so the panel can filter if needed
            self._main_bus.emit(EventType.AUDIO_LEVEL_UPDATE, {**d, "source": "mic"})
        elif event_type in (EventType.ERROR_OCCURRED, EventType.WARNING_OCCURRED):
            self._main_bus.emit(event_type, d)
        # All other pipeline events (STATE_CHANGED, VOICE_CLONE_*, TTS_*, etc.)
        # are deliberately NOT forwarded to the main bus.


# ── Mic translator ─────────────────────────────────────────────────────────


class MicTranslator:
    """
    Self-contained session manager for the mic→translate→virtual-mic pipeline.

    Usage::

        translator = MicTranslator(settings, event_bus)
        await translator.start(
            elevenlabs_service=orchestrator.elevenlabs_service,
            mic_device_id="3",          # sounddevice input index
            target_language="es",
            output_device_id="7",       # CABLE Input sounddevice index
        )
        # …later…
        await translator.stop()
    """

    def __init__(self, settings: AppSettings, event_bus: EventBus) -> None:
        self._settings = settings
        self._main_bus = event_bus
        self._is_running = False

        # Components — created fresh on each start()
        self._mic_capture: Any = None   # MicCapture
        self._pipeline: Any = None      # ProcessingPipeline
        self._playback: Any = None      # AudioPlayback (virtual cable output)
        self._monitor_playback: Any = None  # AudioPlayback (speaker monitor)
        self._pipeline_bus: _MicPipelineBus | None = None

    # ── Public API ──────────────────────────────────────────────────────

    async def start(
        self,
        elevenlabs_service: Any,
        mic_device_id: str | None,
        target_language: str,
        output_device_id: str | None,
        source_language: str = "auto",
        premade_voice_id: str | None = None,
        monitor_device_id: str | None = None,
    ) -> None:
        """
        Start the mic translation pipeline.

        Args:
            elevenlabs_service: Active ElevenLabs / BackendProxy service
                                 (pass ``orchestrator.elevenlabs_service``).
            mic_device_id:      sounddevice input device index as a string,
                                or ``None`` / ``""`` for system default mic.
            target_language:    BCP-47 language code for output (e.g. ``"es"``).
            output_device_id:   sounddevice output device index for CABLE Input,
                                or ``None`` for system default output.
            source_language:    Source language code or ``"auto"`` for detection.
            premade_voice_id:   ElevenLabs voice ID to use for TTS output.
                                Falls back to ``settings.voice_clone.use_premade_voice_id``
                                and then to the ElevenLabs Rachel fallback voice.

        Raises:
            RuntimeError: If MicCapture or AudioPlayback cannot be opened.
        """
        if self._is_running:
            logger.warning("MicTranslator already running — ignoring start()")
            return

        logger.info(
            "Starting MicTranslator",
            mic_device=mic_device_id,
            target_lang=target_language,
            source_lang=source_language,
            output_device=output_device_id,
            monitor_device=monitor_device_id,
        )

        # 1. Create the isolated event bus
        self._pipeline_bus = _MicPipelineBus(self._main_bus)

        # 2. Build pipeline config (no voice cloning for mic mode)
        from live_dubbing.processing.pipeline import PipelineConfig, ProcessingPipeline

        voice_id = (
            premade_voice_id
            or (self._settings.voice_clone.use_premade_voice_id or "").strip()
            or None
        )
        config = PipelineConfig(
            target_language=target_language,
            source_language=source_language,
            auto_clone_voice=False,     # Never clone the user's own mic voice
            use_premade_voice_id=voice_id,
            min_voice_capture_sec=0,    # Not used
            voice_stability=self._settings.voice_clone.voice_stability,
            voice_similarity=self._settings.voice_clone.voice_similarity,
        )
        self._pipeline = ProcessingPipeline(
            elevenlabs_service=elevenlabs_service,
            event_bus=self._pipeline_bus,
            config=config,
        )

        # 3. Create and start audio playback → targets CABLE Input
        from live_dubbing.audio.playback import AudioPlayback

        self._playback = AudioPlayback(sample_rate=24000)
        await self._playback.start(device_id=output_device_id)

        # 3b. Optionally create monitor playback -> targets speakers
        if monitor_device_id:
            from live_dubbing.audio.playback import AudioPlayback as AP
            self._monitor_playback = AP(sample_rate=24000)
            try:
                await self._monitor_playback.start(device_id=monitor_device_id)
                logger.info("Monitor playback started", device=monitor_device_id)
            except Exception as e:
                logger.warning("Failed to start monitor playback", error=str(e))
                self._monitor_playback = None

        # 4. Start pipeline (tasks begin but no audio yet)
        try:
            await self._pipeline.start(on_output=self._on_audio_output)
        except Exception as e:
            # Clean up playback if pipeline fails
            await self._playback.stop()
            self._playback = None
            if self._monitor_playback:
                await self._monitor_playback.stop()
                self._monitor_playback = None
            self._pipeline = None
            self._pipeline_bus = None
            raise RuntimeError(f"Failed to start mic pipeline: {e}") from e

        # 5. Start mic capture (audio starts flowing)
        from live_dubbing.audio.mic_capture import MicCapture

        self._mic_capture = MicCapture(
            sample_rate=self._settings.audio.sample_rate,
            chunk_size_ms=self._settings.audio.chunk_size_ms,
        )
        try:
            await self._mic_capture.start(
                device_id=mic_device_id,
                on_audio_chunk=self._on_mic_chunk,
            )
        except Exception as e:
            # Clean up everything on capture failure
            await self._pipeline.stop()
            await self._playback.stop()
            if self._monitor_playback:
                await self._monitor_playback.stop()
                self._monitor_playback = None
            self._mic_capture = None
            self._pipeline = None
            self._playback = None
            self._pipeline_bus = None
            raise RuntimeError(f"Failed to open microphone: {e}") from e

        self._is_running = True
        self._main_bus.emit(
            EventType.MIC_TRANSLATE_STARTED,
            {
                "target_language": target_language,
                "source_language": source_language,
                "mic_device_id": mic_device_id,
                "output_device_id": output_device_id,
                "monitor_device_id": monitor_device_id,
            },
        )
        logger.info("MicTranslator started successfully")

    async def stop(self) -> None:
        """Stop all components and release devices."""
        if not self._is_running:
            return

        logger.info("Stopping MicTranslator")
        self._is_running = False

        # Stop mic capture first — no more audio entering the pipeline
        if self._mic_capture is not None:
            try:
                await self._mic_capture.stop()
            except Exception as e:
                logger.debug("Error stopping mic capture", error=str(e))
            self._mic_capture = None

        # Stop pipeline
        if self._pipeline is not None:
            try:
                await self._pipeline.stop()
            except Exception as e:
                logger.debug("Error stopping mic pipeline", error=str(e))
            self._pipeline = None

        # Stop playback
        if self._playback is not None:
            try:
                await self._playback.stop()
            except Exception as e:
                logger.debug("Error stopping mic playback", error=str(e))
            self._playback = None

        # Stop monitor playback
        if self._monitor_playback is not None:
            try:
                await self._monitor_playback.stop()
            except Exception as e:
                logger.debug("Error stopping monitor playback", error=str(e))
            self._monitor_playback = None

        self._pipeline_bus = None

        self._main_bus.emit(EventType.MIC_TRANSLATE_STOPPED, {})
        logger.info("MicTranslator stopped")

    @property
    def is_running(self) -> bool:
        """Return ``True`` while the pipeline is active."""
        return self._is_running

    # ── Private callbacks ───────────────────────────────────────────────

    async def _on_mic_chunk(self, audio_data: bytes, timestamp_ms: int) -> None:
        """Forward incoming mic audio to the processing pipeline."""
        if self._pipeline is not None:
            await self._pipeline.process_chunk(audio_data, timestamp_ms)

    async def _on_audio_output(self, audio_data: bytes) -> None:
        """Forward processed TTS audio to playback devices."""
        # Play to virtual cable (primary output for other apps)
        if self._playback is not None:
            await self._playback.play(audio_data)
        # Also play to monitor speakers so user can hear themselves
        if self._monitor_playback is not None:
            await self._monitor_playback.play(audio_data)
