"""Audio playback module using sounddevice."""

import queue
import threading

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]
import structlog

logger = structlog.get_logger(__name__)


def get_output_devices() -> list[tuple[str, str]]:
    """List sounddevice output devices as (device_id, display_name).

    Returns a list of (device_id, display_name) tuples.
    Always includes "Default" as the first option.
    Filters to show only primary audio APIs (WASAPI/MME) and deduplicates.
    """
    devices = [("", "Default")]
    seen_names: set[str] = set()

    try:
        all_devs = sd.query_devices()
        if all_devs is None:
            return devices
        # Ensure we have an iterable
        if not hasattr(all_devs, "__iter__"):
            return devices

        # Get host API info to filter by preferred APIs
        preferred_apis = {"Windows WASAPI", "MME"}
        _api_indices: set[int] = set()
        api_indices: set[int] | None
        try:
            for api_idx in range(sd.query_hostapis().__len__()):
                api_info = sd.query_hostapis(api_idx)
                if isinstance(api_info, dict):
                    api_name = api_info.get("name", "")
                    if api_name in preferred_apis:
                        _api_indices.add(api_idx)
            api_indices = _api_indices
        except Exception:
            # If we can't get API info, accept all devices
            api_indices = None

        for i, dev in enumerate(all_devs):
            try:
                if not isinstance(dev, dict):
                    continue
                if dev.get("max_output_channels", 0) <= 0:
                    continue

                # Filter by preferred host APIs if available
                if api_indices is not None:
                    dev_api = dev.get("hostapi", -1)
                    if dev_api not in api_indices:
                        continue

                name = dev.get("name", f"Device {i}")
                # Sanitize name for display (handle encoding issues)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                name = str(name)[:50]  # Limit length

                # Skip duplicates (same name)
                if name in seen_names:
                    continue
                seen_names.add(name)

                dev_id = str(i)
                devices.append((dev_id, name))

                # Limit to reasonable number of devices
                if len(devices) >= 25:
                    break

            except Exception:  # pylint: disable=broad-exception-caught
                # Skip problematic device entries
                continue
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Could not list output devices", error=str(e))
    return devices


class AudioPlayback:
    """
    Handles audio output playback using sounddevice.

    Supports streaming playback for low-latency audio output.
    """

    def __init__(
        self,
        sample_rate: int = 24000,  # ElevenLabs default
        channels: int = 1,
        buffer_size_ms: int = 100,
    ) -> None:
        """
        Initialize audio playback.

        Args:
            sample_rate: Sample rate in Hz
            channels: Number of audio channels
            buffer_size_ms: Buffer size in milliseconds
        """
        self._sample_rate = sample_rate
        self._channels = channels
        self._buffer_size_ms = buffer_size_ms

        # State
        self._is_playing = False
        self._playback_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)
        self._device_index: int | None = None

        # Stream
        self._stream: sd.OutputStream | None = None

    async def start(self, device_id: str | None = None) -> None:
        """
        Start audio playback.

        Args:
            device_id: Output device ID (None for default)
        """
        if self._is_playing:
            logger.warning("Playback already running")
            return

        self._device_index = int(device_id) if device_id else None

        logger.info(
            "Starting audio playback",
            device=device_id,
            sample_rate=self._sample_rate,
        )

        self._is_playing = True

        # Start playback thread
        self._playback_thread = threading.Thread(
            target=self._playback_loop,
            daemon=True,
        )
        self._playback_thread.start()

    async def stop(self) -> None:
        """Stop audio playback."""
        if not self._is_playing:
            return

        logger.info("Stopping audio playback")
        self._is_playing = False

        # Push a sentinel so the thread wakes up from queue.get() immediately
        try:
            self._audio_queue.put_nowait(b"")
        except queue.Full:
            pass

        # Wait for thread to finish
        if self._playback_thread:
            self._playback_thread.join(timeout=3.0)
            self._playback_thread = None

        # Clear queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    async def play(self, audio_data: bytes) -> None:
        """
        Queue audio data for playback.

        Args:
            audio_data: Raw audio bytes (float32)
        """
        if not self._is_playing or not audio_data:
            return

        try:
            self._audio_queue.put_nowait(audio_data)
        except queue.Full:
            # Drop oldest if queue full
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(audio_data)
            except queue.Empty:
                pass

    async def play_array(self, audio_array: np.ndarray) -> None:
        """
        Queue numpy array for playback.

        Args:
            audio_array: Audio data as numpy array
        """
        audio_bytes = audio_array.astype(np.float32).tobytes()
        await self.play(audio_bytes)

    def _playback_loop(self) -> None:
        """Run main playback loop in a separate thread."""
        try:
            # Calculate blocksize
            blocksize = int(self._sample_rate * self._buffer_size_ms / 1000)

            # Prefer WASAPI or MME output device
            device_to_use = self._device_index
            if device_to_use is None:
                try:
                    devices = sd.query_devices()
                    for i, dev in enumerate(devices):
                        if not isinstance(dev, dict):
                            continue
                        if dev.get("max_output_channels", 0) <= 0:
                            continue
                        hostapi_idx = dev.get("hostapi", 0)
                        hostapi = sd.query_hostapis(hostapi_idx)
                        if not isinstance(hostapi, dict):
                            continue
                        api_name = str(hostapi.get("name", "")).lower()
                        if "wasapi" in api_name or "mme" in api_name:
                            device_to_use = i
                            logger.info(
                                "Selected output device",
                                device=dev.get("name"),
                                hostapi=api_name,
                            )
                            break
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "Could not query devices", error=str(e)
                    )

            try:
                self._stream = sd.OutputStream(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    dtype=np.float32,
                    blocksize=blocksize,
                    device=device_to_use,
                    latency="high",
                )
                self._stream.start()
            except sd.PortAudioError as e:
                logger.warning(
                    "Failed to open stream with device, trying default",
                    error=str(e),
                )
                self._stream = sd.OutputStream(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    dtype=np.float32,
                    blocksize=blocksize,
                    latency="high",
                )
                self._stream.start()

            logger.info("Playback stream started")

            # Buffer for accumulating audio
            audio_buffer = np.array([], dtype=np.float32)

            while self._is_playing:
                try:
                    # Get audio from queue
                    try:
                        audio_bytes = self._audio_queue.get(timeout=0.1)
                        audio_chunk = np.frombuffer(
                            audio_bytes, dtype=np.float32
                        )
                        audio_buffer = np.concatenate(
                            [audio_buffer, audio_chunk]
                        )
                    except queue.Empty:
                        continue

                    # Play when we have enough data
                    while len(audio_buffer) >= blocksize:
                        chunk = audio_buffer[:blocksize]
                        audio_buffer = audio_buffer[blocksize:]

                        # Reshape for mono output
                        chunk = chunk.reshape(-1, self._channels)
                        self._stream.write(chunk)

                except Exception as e:  # pylint: disable=broad-exception-caught
                    if self._is_playing:
                        logger.exception("Playback error", error=str(e))
                    break

            # Play remaining audio
            if len(audio_buffer) > 0:
                try:
                    chunk = audio_buffer.reshape(-1, self._channels)
                    self._stream.write(chunk)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Playback loop error", error=str(e))
        finally:
            self._is_playing = False
            self._cleanup_stream()

    def _cleanup_stream(self) -> None:
        """Release stream resources safely.

        Uses abort() instead of stop() to avoid blocking on pending
        buffers, and catches any access-violation that PortAudio may
        raise during teardown on Windows.
        """
        stream = self._stream
        self._stream = None  # Clear first to prevent double-cleanup
        if stream:
            try:
                stream.abort()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            try:
                stream.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    @property
    def is_playing(self) -> bool:
        """Check if playback is active."""
        return self._is_playing

    @property
    def sample_rate(self) -> int:
        """Get sample rate."""
        return self._sample_rate

    @property
    def queue_size(self) -> int:
        """Get current queue size."""
        return self._audio_queue.qsize()


class AudioMixer:
    """Mix multiple audio sources into one."""

    def __init__(self, sample_rate: int = 24000) -> None:
        """Initialize mixer with sample rate."""
        self._sample_rate = sample_rate
        self._sources: dict[str, np.ndarray] = {}

    def add_source(self, name: str, audio: np.ndarray) -> None:
        """Add audio source to mix."""
        self._sources[name] = audio

    def mix(self) -> np.ndarray:
        """Mix all sources together."""
        if not self._sources:
            return np.array([], dtype=np.float32)

        # Find max length
        max_len = max(len(audio) for audio in self._sources.values())

        # Mix with zero-padding
        result = np.zeros(max_len, dtype=np.float32)
        for audio in self._sources.values():
            padded = np.zeros(max_len, dtype=np.float32)
            padded[: len(audio)] = audio
            result += padded

        # Normalize to prevent clipping
        max_val = np.max(np.abs(result))
        if max_val > 1.0:
            result = result / max_val

        return result

    def clear(self) -> None:
        """Clear all sources."""
        self._sources.clear()
