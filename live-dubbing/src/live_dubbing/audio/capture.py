"""
WASAPI audio capture using pyaudiowpatch.
"""

import asyncio
import contextlib
import queue
import threading
import time
from collections.abc import Awaitable, Callable

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Type alias for audio callback
AudioCallback = Callable[[bytes, int], Awaitable[None]]


class AudioCapture:
    """
    Captures audio from a specified device using WASAPI.

    Uses pyaudiowpatch for Windows WASAPI loopback capture,
    which allows capturing audio from output devices.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size_ms: int = 100,
    ) -> None:
        """
        Initialize audio capture.

        Args:
            sample_rate: Sample rate in Hz (default 16000 for speech)
            channels: Number of audio channels (default 1 for mono)
            chunk_size_ms: Size of audio chunks in milliseconds
        """
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_size_ms = chunk_size_ms

        # Calculate chunk size in frames
        self._chunk_frames = int(sample_rate * chunk_size_ms / 1000)

        # State
        self._is_capturing = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)
        self._callback: AudioCallback | None = None
        self._device_index: int | None = None
        self._consumer_task: asyncio.Task | None = None

        # PyAudio instance
        self._pa = None
        self._stream = None

    async def start(
        self,
        device_id: str | None = None,
        on_audio_chunk: AudioCallback | None = None,
    ) -> None:
        """
        Start audio capture.

        Args:
            device_id: Device ID/index to capture from (None for default)
            on_audio_chunk: Async callback for each audio chunk
        """
        if self._is_capturing.is_set():
            logger.warning("Capture already running")
            return

        self._callback = on_audio_chunk
        self._device_index = int(device_id) if device_id else None

        logger.info(
            "Starting audio capture",
            device=device_id,
            sample_rate=self._sample_rate,
            chunk_ms=self._chunk_size_ms,
        )

        self._is_capturing.set()

        # Start capture in separate thread
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
        )
        self._capture_thread.start()

        # Start async consumer (keep reference to prevent garbage collection)
        self._consumer_task = asyncio.create_task(self._consume_audio())

    async def stop(self) -> None:
        """Stop audio capture."""
        if not self._is_capturing.is_set():
            return

        logger.info("Stopping audio capture")
        self._is_capturing.clear()

        # Cancel consumer task
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            self._consumer_task = None

        # Wait for capture thread to finish
        if self._capture_thread:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

        # Clear queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def _capture_loop(self) -> None:
        """Main capture loop running in separate thread."""
        try:
            import pyaudiowpatch as pyaudio

            self._pa = pyaudio.PyAudio()

            # Get device info
            assert self._pa is not None
            if self._device_index is not None:
                device_info = self._pa.get_device_info_by_index(self._device_index)
            else:
                # Use default WASAPI loopback device
                device_info = self._get_default_loopback_device()

            if not device_info:
                error_msg = "No capture device available. Please check VB-Cable installation."
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            logger.info(
                "Using capture device",
                name=device_info.get("name"),
                index=device_info.get("index"),
            )

            # Get device sample rate
            device_sample_rate = int(device_info.get("defaultSampleRate", 44100))
            device_channels = min(
                int(device_info.get("maxInputChannels", 2)), 2
            )

            # Open stream (self._pa asserted above)
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=device_channels,
                rate=device_sample_rate,
                input=True,
                input_device_index=device_info.get("index"),
                frames_per_buffer=int(device_sample_rate * self._chunk_size_ms / 1000),
            )

            logger.info(
                "Capture stream opened",
                device_rate=device_sample_rate,
                target_rate=self._sample_rate,
            )

            # Capture loop
            assert self._stream is not None
            while self._is_capturing.is_set():
                try:
                    # Read audio data
                    data = self._stream.read(
                        int(device_sample_rate * self._chunk_size_ms / 1000),
                        exception_on_overflow=False,
                    )

                    timestamp_ms = int(time.time() * 1000)

                    # Convert to numpy array
                    audio_data = np.frombuffer(data, dtype=np.float32)

                    # Convert to mono if needed
                    if device_channels > 1:
                        audio_data = audio_data.reshape(-1, device_channels)
                        audio_data = np.mean(audio_data, axis=1)

                    # Resample if needed
                    if device_sample_rate != self._sample_rate:
                        audio_data = self._resample(
                            audio_data, device_sample_rate, self._sample_rate
                        )

                    # Debug: Log audio levels periodically
                    if not hasattr(self, '_chunk_count'):
                        self._chunk_count = 0
                    self._chunk_count += 1
                    if self._chunk_count <= 3 or self._chunk_count % 100 == 0:
                        level = np.sqrt(np.mean(audio_data ** 2))
                        max_val = np.max(np.abs(audio_data))
                        logger.info(
                            "Audio chunk captured",
                            chunk_num=self._chunk_count,
                            rms_level=f"{level:.4f}",
                            max_amplitude=f"{max_val:.4f}",
                            samples=len(audio_data),
                        )

                    # Convert to bytes
                    audio_bytes = audio_data.astype(np.float32).tobytes()

                    # Put in queue for async processing
                    try:
                        self._audio_queue.put_nowait((audio_bytes, timestamp_ms))
                    except queue.Full:
                        # Drop oldest chunk if queue is full
                        try:
                            self._audio_queue.get_nowait()
                            self._audio_queue.put_nowait((audio_bytes, timestamp_ms))
                        except queue.Empty:
                            pass

                except Exception as e:
                    if not self._is_capturing.is_set():
                        break
                    # Transient errors (e.g. WASAPI -9999 host error) are
                    # common during device transitions.  Retry a few times
                    # before giving up.
                    if not hasattr(self, '_error_count'):
                        self._error_count = 0
                    self._error_count += 1
                    logger.error(
                        "Error reading audio",
                        error=str(e),
                        attempt=self._error_count,
                    )
                    if self._error_count >= 5:
                        logger.error("Too many capture errors, stopping")
                        break
                    time.sleep(0.1)  # Brief pause before retry
                    continue

        except ImportError:
            logger.error("pyaudiowpatch not installed")
        except Exception as e:
            logger.exception("Capture loop error", error=str(e))
        finally:
            self._cleanup_stream()

    def _get_default_loopback_device(self) -> dict | None:
        """Get default WASAPI loopback device."""
        try:
            if self._pa is None:
                return None

            # Find WASAPI loopback device
            for i in range(self._pa.get_device_count()):
                device = self._pa.get_device_info_by_index(i)

                # Look for loopback device
                if device.get("maxInputChannels", 0) > 0:
                    name = device.get("name", "").lower()
                    if "loopback" in name or "stereo mix" in name:
                        return device  # type: ignore[return-value]

            # Fall back to any input device
            default_input = self._pa.get_default_input_device_info()
            return default_input  # type: ignore[return-value]

        except Exception as e:
            logger.exception("Failed to get loopback device", error=str(e))
            return None

    def _resample(
        self, audio: np.ndarray, orig_rate: int, target_rate: int
    ) -> np.ndarray:
        """Resample audio to target sample rate."""
        if orig_rate == target_rate:
            return audio

        try:
            from scipy import signal

            # Calculate number of samples in resampled audio
            num_samples = int(len(audio) * target_rate / orig_rate)
            resampled: np.ndarray = signal.resample(audio, num_samples)
            return resampled.astype(np.float32)

        except ImportError:
            # Simple linear interpolation fallback
            ratio = target_rate / orig_rate
            indices = np.arange(0, len(audio), 1 / ratio)
            indices = np.clip(indices, 0, len(audio) - 1).astype(int)
            result: np.ndarray = audio[indices]
            return result

    def _cleanup_stream(self) -> None:
        """Clean up audio stream resources."""
        if self._stream:
            with contextlib.suppress(Exception):
                self._stream.stop_stream()
                self._stream.close()
            self._stream = None

        if self._pa:
            with contextlib.suppress(Exception):
                self._pa.terminate()
            self._pa = None

    async def _consume_audio(self) -> None:
        """Async consumer for audio queue."""
        logger.info("Audio consumer started")
        consume_count = 0
        while self._is_capturing.is_set():
            try:
                # Non-blocking get with timeout
                try:
                    audio_bytes, timestamp_ms = self._audio_queue.get_nowait()
                    consume_count += 1

                    if consume_count <= 3 or consume_count % 100 == 0:
                        logger.info(
                            "Audio consumer forwarding chunk",
                            count=consume_count,
                            has_callback=self._callback is not None,
                        )

                    if self._callback:
                        logger.debug(
                            "Invoking audio callback",
                            count=consume_count,
                            chunk_bytes=len(audio_bytes),
                            ts_ms=timestamp_ms,
                        )
                        await self._callback(audio_bytes, timestamp_ms)
                        if consume_count <= 3 or consume_count % 100 == 0:
                            logger.info("Audio callback completed", count=consume_count)
                    else:
                        if consume_count <= 5:
                            logger.warning("No callback set; chunk dropped", count=consume_count)

                except queue.Empty:
                    await asyncio.sleep(0.01)

            except Exception as e:
                logger.exception("Error in audio consumer", error=str(e))
                await asyncio.sleep(0.1)

    @property
    def is_capturing(self) -> bool:
        """Check if capture is running."""
        return self._is_capturing.is_set()

    @property
    def sample_rate(self) -> int:
        """Get sample rate."""
        return self._sample_rate

    @property
    def chunk_size_ms(self) -> int:
        """Get chunk size in milliseconds."""
        return self._chunk_size_ms

    def get_audio_level(self, audio_bytes: bytes) -> float:
        """
        Calculate audio level (RMS) from audio bytes.

        Args:
            audio_bytes: Raw audio data as bytes

        Returns:
            RMS level (0.0 to 1.0)
        """
        try:
            audio = np.frombuffer(audio_bytes, dtype=np.float32)
            rms: float = float(np.sqrt(np.mean(audio ** 2)))
            return min(1.0, rms)
        except Exception:
            return 0.0
