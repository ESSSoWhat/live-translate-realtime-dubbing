"""
Core Orchestrator - Coordinates all subsystems and manages application state.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from live_dubbing.audio.session import AudioSessionInfo
from live_dubbing.config.settings import AppSettings, redact_secrets
from live_dubbing.core.events import Event, EventBus, EventType
from live_dubbing.core.state import (
    ApplicationStateSnapshot,
    AppState,
    PipelineStats,
    TranslationConfig,
    TranslationState,
)

if TYPE_CHECKING:
    from live_dubbing.audio.capture import AudioCapture
    from live_dubbing.audio.playback import AudioPlayback
    from live_dubbing.audio.routing import VirtualAudioRouter
    from live_dubbing.processing.pipeline import ProcessingPipeline
    from live_dubbing.services.elevenlabs_service import ElevenLabsService
    from live_dubbing.services.voice_cloning import ClonedVoice

logger = structlog.get_logger(__name__)


class Orchestrator:
    """
    Central coordinator managing all subsystems.

    Responsibilities:
    - Coordinate audio capture, processing, and playback
    - Manage application state transitions
    - Handle voice cloning workflow
    - Emit events for UI updates
    """

    def __init__(self, settings: AppSettings, event_bus: EventBus) -> None:
        self._settings = settings
        self._event_bus = event_bus

        # State
        self._app_state = AppState.INITIALIZING
        self._translation_state = TranslationState.IDLE
        self._translation_config: TranslationConfig | None = None
        self._pipeline_stats = PipelineStats()

        # Subsystem references (initialized later)
        self._audio_capture: AudioCapture | None = None
        self._audio_routing: VirtualAudioRouter | None = None
        self._audio_playback: AudioPlayback | None = None
        self._processing_pipeline: ProcessingPipeline | None = None
        self._elevenlabs_service: ElevenLabsService | None = None

        # Detected sessions
        self._audio_sessions: list[AudioSessionInfo] = []

        # Runtime state
        self._vb_cable_installed = False
        self._process_loopback_supported = False
        self._is_initialized = False
        self._event_unsubscribers: list[Callable[[], None]] = []
        self._no_audio_check_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Initialize all subsystems."""
        logger.info("Initializing orchestrator")

        try:
            # Check for VB-Cable
            await self._check_vb_cable()

            # Initialize audio subsystems
            await self._init_audio_subsystems()

            # Initialize ElevenLabs service
            await self._init_elevenlabs()

            # Initialize processing pipeline
            await self._init_pipeline()

            # Update translation state when pipeline completes voice clone (premade or cloned)
            unsub = self._event_bus.subscribe(
                EventType.VOICE_CLONE_COMPLETED,
                self._on_voice_clone_completed,
            )
            self._event_unsubscribers.append(unsub)

            # Enumerate audio sessions
            await self._refresh_audio_sessions()

            # Set state to ready
            self._set_app_state(AppState.READY)
            self._is_initialized = True

            self._event_bus.emit(EventType.APP_INITIALIZED, {})
            logger.info("Orchestrator initialized successfully")

        except Exception as e:
            logger.exception("Failed to initialize orchestrator", error=str(e))
            self._set_app_state(AppState.ERROR)
            self._event_bus.emit_error(f"Initialization failed: {redact_secrets(str(e))}")

    async def shutdown(self) -> None:
        """Shutdown all subsystems."""
        logger.info("Shutting down orchestrator")

        # Stop translation if active (running, cloning, or translating)
        if self._translation_state != TranslationState.IDLE:
            await self.stop_translation()

        # Unsubscribe from events
        for unsub in self._event_unsubscribers:
            unsub()
        self._event_unsubscribers.clear()

        # Cleanup subsystems
        if self._processing_pipeline:
            await self._processing_pipeline.stop()
        if self._audio_capture:
            await self._audio_capture.stop()
        if self._audio_playback:
            await self._audio_playback.stop()

        self._event_bus.emit(EventType.APP_SHUTDOWN, {})
        logger.info("Orchestrator shutdown complete")

    async def _check_vb_cable(self) -> None:
        """Check if VB-Audio Virtual Cable is installed and if process loopback is supported."""
        from live_dubbing.audio.routing import VirtualAudioRouter

        router = VirtualAudioRouter()
        router.detect_virtual_devices()
        self._vb_cable_installed = router.get_vb_cable() is not None
        self._process_loopback_supported = router.is_process_loopback_supported()

        if not self._vb_cable_installed:
            logger.info("VB-Cable not detected; system audio capture available")
        if self._process_loopback_supported:
            logger.info("Process loopback supported (Windows 10 21H2+)")

    async def _init_audio_subsystems(self) -> None:
        """Initialize audio capture, routing, and playback."""
        from live_dubbing.audio.capture import AudioCapture
        from live_dubbing.audio.playback import AudioPlayback
        from live_dubbing.audio.routing import VirtualAudioRouter

        self._audio_routing = VirtualAudioRouter()
        self._audio_capture = AudioCapture(
            sample_rate=self._settings.audio.sample_rate,
            chunk_size_ms=self._settings.audio.chunk_size_ms,
        )
        self._audio_playback = AudioPlayback(
            sample_rate=24000,  # ElevenLabs output rate
            volume=self._settings.audio.output_volume,
        )

    async def _init_elevenlabs(self) -> None:
        """Initialize ElevenLabs service or BackendProxyService.

        Prefers direct ElevenLabs access when API key is available (faster,
        no backend dependency). Falls back to BackendProxyService when only
        auth token is present (production / monetised path).
        """
        from live_dubbing.services.elevenlabs_service import ElevenLabsService

        # ── Direct ElevenLabs access (preferred when API key available) ───
        api_key = self._settings.get_elevenlabs_api_key()
        if api_key:
            openai_key = self._settings.get_openai_api_key()
            self._elevenlabs_service = ElevenLabsService(
                api_key=api_key,
                openai_api_key=openai_key,
            )
            logger.info("ElevenLabs service initialised (direct API key)")
            return

        # ── Monetised path: use backend proxy ────────────────────────────
        if self._settings.is_token_valid():
            from live_dubbing.services.backend_service import BackendProxyService

            def _on_token_refreshed(access: str, refresh: str) -> None:
                self._settings.set_auth_tokens(access, refresh)

            self._elevenlabs_service = BackendProxyService(
                base_url=self._settings.get_backend_url(),
                access_token=self._settings.get_access_token() or "",
                refresh_token=self._settings.get_refresh_token() or "",
                on_token_refreshed=_on_token_refreshed,
            )
            logger.info("Backend proxy service initialised")
            return

        logger.warning("No ElevenLabs API key or auth token configured")

    async def reinit_elevenlabs(self) -> None:
        """Re-read API keys from settings and recreate the ElevenLabs service."""
        await self._init_elevenlabs()
        # Re-create pipeline so it uses the new service instance
        await self._init_pipeline()

    async def _init_pipeline(self) -> None:
        """Initialize processing pipeline."""
        from live_dubbing.processing.pipeline import PipelineConfig, ProcessingPipeline

        pipeline_config = PipelineConfig(
            target_language=self._settings.translation.default_target_language,
            source_language="auto",
            min_voice_capture_sec=self._settings.voice_clone.dynamic_capture_duration_sec,
            voice_stability=self._settings.voice_clone.voice_stability,
            voice_similarity=self._settings.voice_clone.voice_similarity,
            use_premade_voice_id=self._settings.voice_clone.use_premade_voice_id,
            auto_clone_voice=self._settings.voice_clone.auto_clone_voice,
        )
        self._processing_pipeline = ProcessingPipeline(
            elevenlabs_service=self._elevenlabs_service,
            event_bus=self._event_bus,
            config=pipeline_config,
        )

    async def _refresh_audio_sessions(self) -> None:
        """Refresh list of available audio sessions."""
        from live_dubbing.audio.session import AudioSessionEnumerator

        enumerator = AudioSessionEnumerator()
        # Use combined method to get all processes, not just active audio sessions
        self._audio_sessions = enumerator.get_sessions_combined()

        for session in self._audio_sessions:
            self._event_bus.emit(
                EventType.AUDIO_SESSION_DETECTED,
                {"session": session},
            )

    def get_audio_sessions(self) -> list[AudioSessionInfo]:
        """Get list of available audio sessions."""
        return self._audio_sessions.copy()

    async def refresh_audio_sessions(self) -> list[AudioSessionInfo]:
        """Refresh and return audio sessions."""
        await self._refresh_audio_sessions()
        return self._audio_sessions.copy()

    async def start_translation(
        self,
        target_app: AudioSessionInfo,
        target_language: str,
        source_language: str = "auto",
        use_system_fallback: bool = False,
    ) -> None:
        """
        Start the translation workflow.

        Args:
            target_app: The application to capture audio from
            target_language: Target language code
            source_language: Source language code (auto for detection)
            use_system_fallback: If True, use system loopback instead of VB-Cable
        """
        if self._app_state != AppState.READY:
            raise RuntimeError(f"Cannot start translation in state: {self._app_state}")

        # Only require VB-Cable if not using fallback and process loopback not supported
        if not use_system_fallback and not self._process_loopback_supported and not self._vb_cable_installed:
            raise RuntimeError("VB-Cable not installed")

        if not self._elevenlabs_service:
            raise RuntimeError("ElevenLabs API key not configured")

        logger.info(
            "Starting translation",
            target_app=target_app.name,
            source_lang=source_language,
            target_lang=target_language,
            use_fallback=use_system_fallback,
        )

        self._translation_config = TranslationConfig(
            target_app=target_app,
            source_language=source_language,
            target_language=target_language,
        )

        self._set_app_state(AppState.RUNNING)
        self._set_translation_state(TranslationState.WAITING_FOR_AUDIO)

        try:
            # Configure audio routing based on mode
            device_id = None
            capture_pid = None

            if use_system_fallback:
                from live_dubbing.audio.routing import CaptureMode

                if self._audio_routing is None:
                    raise RuntimeError("Audio routing not initialized")
                self._audio_routing.configure_system_loopback()
                device_id = self._audio_routing.get_capture_device_id()
                self._audio_routing.set_capture_mode(CaptureMode.SYSTEM_LOOPBACK)
                logger.info("Using system loopback capture", device_id=device_id)
            elif self._process_loopback_supported:
                # Use native process loopback (no VB-Cable)
                if self._audio_routing is None:
                    raise RuntimeError("Audio routing not initialized")
                self._audio_routing.configure_process_loopback(target_app.pid)
                capture_pid = self._audio_routing.get_target_pid()
                logger.info("Using process loopback capture", pid=capture_pid)
            else:
                # Use VB-Cable routing
                if self._audio_routing is None:
                    raise RuntimeError("Audio routing not initialized")
                await self._audio_routing.route_app_to_virtual(target_app.pid)
                device_id = self._audio_routing.get_capture_device_id()

            # Validate capture is available
            if device_id is None and capture_pid is None:
                if use_system_fallback:
                    raise RuntimeError(
                        "No audio capture device available for system loopback."
                    )
                else:
                    raise RuntimeError(
                        "No audio capture device available. "
                        "Please ensure VB-Audio Virtual Cable is properly installed."
                    )

            # Start voice cloning process (dynamic mode)
            self._set_translation_state(TranslationState.CLONING_VOICE)
            self._event_bus.emit(EventType.VOICE_CLONE_STARTED, {})

            # Set pipeline config for this session (languages) - BEFORE starting pipeline
            if self._processing_pipeline:
                from live_dubbing.processing.pipeline import PipelineConfig
                self._processing_pipeline.set_config(PipelineConfig(
                    target_language=target_language,
                    source_language=source_language,
                    min_voice_capture_sec=self._settings.voice_clone.dynamic_capture_duration_sec,
                    voice_stability=self._settings.voice_clone.voice_stability,
                    voice_similarity=self._settings.voice_clone.voice_similarity,
                    use_premade_voice_id=self._settings.voice_clone.use_premade_voice_id,
                    auto_clone_voice=self._settings.voice_clone.auto_clone_voice,
                ))

            # Start the processing pipeline BEFORE audio capture
            # This ensures VAD/STT/TTS tasks are running when audio starts arriving
            if self._processing_pipeline:
                await self._processing_pipeline.start(
                    on_output=self._on_pipeline_output,
                    on_transcription=self._on_pipeline_transcription,
                )
                logger.info("Processing pipeline started")
            else:
                logger.warning("Processing pipeline not initialized")

            # Start audio capture AFTER pipeline is ready
            if self._audio_capture is None:
                raise RuntimeError("Audio capture not initialized")
            on_plb_error = None
            if capture_pid:
                on_plb_error = lambda err: self._event_bus.emit(
                    EventType.PROCESS_LOOPBACK_FAILED, {"error": err}
                )
            await self._audio_capture.start(
                device_id=device_id,
                pid=capture_pid,
                on_audio_chunk=self._on_audio_chunk,
                on_process_loopback_error=on_plb_error,
            )

            # Start playback with configured output device
            if self._audio_playback:
                out_id = self._settings.audio.output_device_id
                self._audio_playback.set_volume(self._settings.audio.output_volume)
                await self._audio_playback.start(device_id=out_id or None)

            capture_mode = (
                "system_loopback"
                if use_system_fallback
                else ("process_loopback" if capture_pid else "vb_cable")
            )
            self._event_bus.emit(
                EventType.AUDIO_CAPTURE_STARTED,
                {"capture_mode": capture_mode},
            )
            self._event_bus.emit(
                EventType.TRANSLATION_STARTED,
                {
                    "target_app": target_app.name,
                    "source_language": source_language,
                    "target_language": target_language,
                    "capture_mode": capture_mode,
                },
            )

            # Start background task to warn if no audio received
            self._no_audio_check_task = asyncio.create_task(
                self._check_no_audio_loop(target_app.name, capture_mode),
            )

        except Exception as e:
            import traceback as _tb
            # Include full traceback in error dialog so we can diagnose the issue
            tb_str = _tb.format_exc()

            # Write full traceback to log file
            try:
                import os as _os
                _log_dir = _os.path.join(
                    _os.environ.get("LOCALAPPDATA", _os.path.expanduser("~")),
                    "Live Translate",
                    "logs",
                )
                _os.makedirs(_log_dir, exist_ok=True)
                _crash_path = _os.path.join(_log_dir, "crash.log")
                with open(_crash_path, "w", encoding="utf-8") as _f:
                    _f.write(f"Failed to start translation: {e}\n\n")
                    _f.write(tb_str)
            except Exception:
                pass

            logger.exception("Failed to start translation", error=str(e))
            self._set_app_state(AppState.ERROR)
            self._set_translation_state(TranslationState.ERROR)
            self._event_bus.emit_error(
                redact_secrets(f"Failed to start translation: {e}\n\nTraceback:\n{tb_str}")
            )
            raise

    async def stop_translation(self) -> None:
        """Stop the current translation session."""
        logger.info("Stopping translation")

        self._set_app_state(AppState.STOPPING)

        # Save PID before try block so routing can be restored even if config is cleared
        target_pid = (
            self._translation_config.target_app.pid
            if self._translation_config is not None
            else None
        )

        try:
            # Cancel no-audio check task
            if self._no_audio_check_task and not self._no_audio_check_task.done():
                self._no_audio_check_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._no_audio_check_task
            self._no_audio_check_task = None

            # Stop audio capture
            if self._audio_capture:
                await self._audio_capture.stop()

            # Restore audio routing
            if self._audio_routing is not None and target_pid is not None:
                await self._audio_routing.restore_original_routing(target_pid)

            # Stop processing pipeline
            if self._processing_pipeline:
                await self._processing_pipeline.stop()

            # Stop playback
            if self._audio_playback:
                await self._audio_playback.stop()

            self._event_bus.emit(EventType.AUDIO_CAPTURE_STOPPED, {})
            self._event_bus.emit(EventType.TRANSLATION_STOPPED, {})

        finally:
            self._translation_config = None
            self._set_translation_state(TranslationState.IDLE)
            self._set_app_state(AppState.READY)

    async def _on_audio_chunk(self, audio_data: bytes, timestamp_ms: int) -> None:
        """Handle incoming audio chunk from capture."""
        if not self._processing_pipeline:
            return

        # Forward to processing pipeline
        await self._processing_pipeline.process_chunk(
            audio_data=audio_data,
            timestamp_ms=timestamp_ms,
        )

        # Update stats
        self._pipeline_stats.total_chunks_processed += 1

    async def _on_pipeline_output(self, audio_data: bytes) -> None:
        """Handle output audio from the processing pipeline."""
        if self._audio_playback:
            await self._audio_playback.play(audio_data)

    async def _on_pipeline_transcription(self, text: str) -> None:
        """Handle transcription from the processing pipeline.

        Note: The pipeline already emits TRANSCRIPTION_UPDATE directly,
        so we only update stats here to avoid duplicate UI updates.
        """
        logger.debug("Transcription received", text=text[:50] if text else "")
        self._pipeline_stats.last_transcription = text

    def _set_app_state(self, new_state: AppState) -> None:
        """Set application state and emit event."""
        if self._app_state != new_state:
            old_state = self._app_state
            self._app_state = new_state
            logger.info("App state changed", old=old_state.name, new=new_state.name)
            self._event_bus.emit_state_change(old_state, new_state)

    def _on_voice_clone_completed(self, _event: Event) -> None:
        """When pipeline finishes voice clone (or uses premade), switch to TRANSLATING."""
        if self._translation_state == TranslationState.CLONING_VOICE:
            self._set_translation_state(TranslationState.TRANSLATING)

    async def fallback_to_system_loopback(self) -> None:
        """
        Switch from failed process loopback to system loopback capture.

        Called when process loopback activation fails (e.g. 0x8000000E).
        """
        if self._app_state != AppState.RUNNING or not self._translation_config:
            return
        if not self._audio_capture or not self._audio_routing:
            return

        target_name = self._translation_config.target_app.name
        logger.info("Process loopback failed, falling back to system loopback", app=target_name)

        # Cancel no-audio check
        if self._no_audio_check_task and not self._no_audio_check_task.done():
            self._no_audio_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._no_audio_check_task
        self._no_audio_check_task = None

        # Stop capture (process loopback thread already exited; stop consumer)
        await self._audio_capture.stop()

        # Configure system loopback
        from live_dubbing.audio.routing import CaptureMode

        try:
            self._audio_routing.configure_system_loopback()
        except RuntimeError as e:
            logger.error("System loopback fallback failed", error=str(e))
            self._event_bus.emit_warning(
                f"Process loopback failed and no system loopback device found: {e} "
                "Use 'All system audio' mode instead."
            )
            await self.stop_translation()
            return

        device_id = self._audio_routing.get_capture_device_id()
        self._audio_routing.set_capture_mode(CaptureMode.SYSTEM_LOOPBACK)

        # Restart capture with device
        await self._audio_capture.start(
            device_id=device_id,
            pid=None,
            on_audio_chunk=self._on_audio_chunk,
        )

        self._event_bus.emit(
            EventType.AUDIO_CAPTURE_STARTED,
            {"capture_mode": "system_loopback", "fallback": True},
        )
        self._event_bus.emit_warning(
            f"Process loopback unavailable. Using all system audio for {target_name}."
        )

        # Restart no-audio check
        self._no_audio_check_task = asyncio.create_task(
            self._check_no_audio_loop(target_name, "system_loopback"),
        )

    def _set_translation_state(self, new_state: TranslationState) -> None:
        """Set translation state and emit event."""
        if self._translation_state != new_state:
            old_state = self._translation_state
            self._translation_state = new_state
            logger.info(
                "Translation state changed", old=old_state.name, new=new_state.name
            )
            self._event_bus.emit(
                EventType.TRANSLATION_STATE_CHANGED,
                {"old_state": old_state, "new_state": new_state},
            )

    async def _check_no_audio_loop(self, app_name: str, capture_mode: str) -> None:
        """Warn user if no audio received after 5 seconds."""
        import time

        warned = False
        start = time.time()
        while True:
            await asyncio.sleep(2.0)
            if not self._translation_config or self._translation_state == TranslationState.IDLE:
                return
            elapsed = time.time() - start
            chunks = (
                self._pipeline_stats.total_chunks_processed
                if self._pipeline_stats
                else 0
            )
            if chunks > 0:
                return  # Audio flowing, no need to warn
            if elapsed >= 5.0 and not warned:
                warned = True
                if capture_mode == "process_loopback":
                    hint = (
                        "Make sure the app is playing sound (e.g. play a video). "
                        "If this persists, switch to 'All system audio'."
                    )
                elif capture_mode == "vb_cable":
                    hint = (
                        f"Route {app_name} to CABLE Input: Sound settings → App volume "
                        f"→ {app_name} → Output: CABLE Input."
                    )
                else:
                    hint = (
                        "Ensure audio is playing through your default output device "
                        "(e.g. play a video in any app)."
                    )
                self._event_bus.emit_warning(
                    f"No audio detected from {app_name}. {hint}",
                    {"app_name": app_name, "capture_mode": capture_mode},
                )
                logger.warning(
                    "No audio chunks received",
                    app=app_name,
                    elapsed=round(elapsed, 1),
                )

    def get_state_snapshot(self) -> ApplicationStateSnapshot:
        """Get current application state snapshot."""
        return ApplicationStateSnapshot(
            app_state=self._app_state,
            translation_state=self._translation_state,
            translation_config=self._translation_config,
            pipeline_stats=self._pipeline_stats,
            vb_cable_installed=self._vb_cable_installed,
            process_loopback_supported=self._process_loopback_supported,
            api_key_configured=self._elevenlabs_service is not None,
        )

    def get_pipeline_queue_depths(self) -> dict[str, int]:
        """
        Get pipeline queue depths for debugging.

        Returns:
            Dict with queue names and their current sizes
        """
        if self._processing_pipeline:
            return self._processing_pipeline.get_queue_depths()
        return {"vad": 0, "stt": 0, "tts": 0, "output": 0}

    @property
    def elevenlabs_service(self):
        """Expose the active ElevenLabs / BackendProxy service for other components."""
        return self._elevenlabs_service

    @property
    def is_vb_cable_installed(self) -> bool:
        """Check if VB-Cable is installed."""
        return self._vb_cable_installed

    @property
    def is_process_loopback_supported(self) -> bool:
        """Check if native process loopback is supported (Windows 10 21H2+)."""
        return self._process_loopback_supported

    @property
    def is_api_key_configured(self) -> bool:
        """Check if ElevenLabs API key is configured."""
        return self._elevenlabs_service is not None

    @property
    def current_state(self) -> AppState:
        """Get current application state."""
        return self._app_state

    @property
    def translation_state(self) -> TranslationState:
        """Get current translation state."""
        return self._translation_state

    # ── Multi-voice management ───────────────────────────────────────────

    def get_saved_voices(self) -> list[ClonedVoice]:
        """Get all cached/saved voices from the pipeline's voice manager."""
        if self._processing_pipeline and self._processing_pipeline._voice_manager:
            return self._processing_pipeline._voice_manager.get_all_cached_voices()
        return []

    async def switch_voice(self, voice_id: str) -> None:
        """Switch the active TTS voice to a previously cloned voice.

        Args:
            voice_id: ElevenLabs voice ID to use
        """
        if not self._processing_pipeline:
            raise RuntimeError("Processing pipeline not initialized")

        vm = self._processing_pipeline._voice_manager
        if not vm:
            raise RuntimeError("Voice manager not initialized")

        voice = vm.get_cached_voice(voice_id)
        if not voice:
            raise RuntimeError(f"Voice {voice_id} not found in cache")

        self._processing_pipeline.set_active_voice(voice)

        # Persist as default
        self._settings.voice_clone.default_voice_id = voice_id
        logger.info("Switched active voice", voice_id=voice_id, name=voice.name)

    async def start_voice_capture(self, speaker_name: str) -> None:
        """Begin capturing audio for a named speaker's voice clone.

        Args:
            speaker_name: Human-readable label for this speaker
        """
        if not self._processing_pipeline:
            raise RuntimeError("Processing pipeline not initialized")

        vm = self._processing_pipeline._voice_manager
        if not vm:
            raise RuntimeError("Voice manager not initialized")

        await vm.start_dynamic_capture(
            sample_rate=self._settings.audio.sample_rate,
            speaker_label=speaker_name,
        )
        self._event_bus.emit(
            EventType.VOICE_CLONE_STARTED,
            {"speaker_name": speaker_name},
        )

    async def finish_voice_capture(self) -> ClonedVoice | None:
        """Finish capturing and create the voice clone.

        Returns:
            The newly cloned voice, or None if capture failed.
        """
        if not self._processing_pipeline:
            return None

        vm = self._processing_pipeline._voice_manager
        if not vm or not vm.is_capturing:
            return None

        try:
            voice = await vm.create_dynamic_clone()
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {"voice_id": voice.voice_id, "name": voice.name},
            )
            return voice
        except Exception as e:
            logger.exception("Voice capture failed", error=str(e))
            self._event_bus.emit_error(f"Voice capture failed: {redact_secrets(str(e))}")
            return None

    async def clone_voice_from_file(self, file_path: str, name: str) -> ClonedVoice | None:
        """Clone a voice from an audio file.

        Args:
            file_path: Path to the audio file (WAV, MP3, etc.)
            name: Name for the cloned voice

        Returns:
            The newly cloned voice, or None on failure.
        """
        if not self._processing_pipeline:
            raise RuntimeError("Processing pipeline not initialized")

        vm = self._processing_pipeline._voice_manager
        if not vm:
            raise RuntimeError("Voice manager not initialized")

        try:
            voice = await vm.create_clone_from_file(
                file_path=file_path,
                name=name,
                speaker_label=name,
            )
            self._event_bus.emit(
                EventType.VOICE_CLONE_COMPLETED,
                {"voice_id": voice.voice_id, "name": voice.name},
            )
            return voice
        except Exception as e:
            logger.exception("Voice clone from file failed", error=str(e))
            self._event_bus.emit_error(f"Voice clone from file failed: {redact_secrets(str(e))}")
            return None

    def rename_voice(self, voice_id: str, new_name: str) -> bool:
        """Rename a saved voice's display name. Returns True if renamed."""
        if not self._processing_pipeline:
            return False
        vm = self._processing_pipeline._voice_manager
        if not vm:
            return False
        return vm.rename_voice(voice_id, new_name)

    def set_output_volume(self, volume: float) -> None:
        """Set TTS playback volume (0.0 to 1.0)."""
        if self._audio_playback:
            self._audio_playback.set_volume(volume)

    def get_output_volume(self) -> float:
        """Get current TTS playback volume."""
        if self._audio_playback:
            return self._audio_playback.get_volume()
        return 1.0

    async def delete_voice(self, voice_id: str) -> bool:
        """Delete a saved voice clone.

        Args:
            voice_id: ElevenLabs voice ID to delete

        Returns:
            True if deletion succeeded
        """
        if not self._processing_pipeline:
            return False

        vm = self._processing_pipeline._voice_manager
        if not vm:
            return False

        success = await vm.cleanup_voice(voice_id)

        # Clear default if we deleted it
        if success and self._settings.voice_clone.default_voice_id == voice_id:
            self._settings.voice_clone.default_voice_id = None

        return success
