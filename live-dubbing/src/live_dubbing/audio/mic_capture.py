"""
Microphone capture using sounddevice.InputStream.

Unlike AudioCapture (which uses pyaudiowpatch for WASAPI loopback from output devices),
MicCapture reads directly from any input device — a real microphone, headset, etc.
"""

import asyncio
import queue
import threading
import time
from collections.abc import Awaitable, Callable

import numpy as np
import sounddevice as sd
import structlog

logger = structlog.get_logger(__name__)

# Re-export the same type alias used by AudioCapture
AudioCallback = Callable[[bytes, int], Awaitable[None]]


def get_input_devices() -> list[tuple[str, str]]:
    """List microphone / input devices as (device_id, display_name) pairs.

    Always includes ``("", "Default Microphone")`` as the first option.
    Filters to WASAPI/MME devices and deduplicates by name.

    Returns:
        List of (device_id, display_name) where device_id is a string integer
        index into sounddevice's device list (same convention as
        ``audio/playback.py::get_output_devices``).
    """
    devices: list[tuple[str, str]] = [("", "Default Microphone")]
    seen_names: set[str] = set()

    try:
        all_devs = sd.query_devices()
        if all_devs is None:
            return devices

        # Build set of preferred host-API indices (WASAPI, MME)
        preferred_apis = {"Windows WASAPI", "MME"}
        _api_indices: set[int] = set()
        api_indices: set[int] | None
        try:
            for api_idx in range(sd.query_hostapis().__len__()):  # type: ignore[union-attr]
                api_info = sd.query_hostapis(api_idx)
                if isinstance(api_info, dict) and api_info.get("name", "") in preferred_apis:
                    _api_indices.add(api_idx)
            api_indices = _api_indices
        except Exception:
            api_indices = None  # Accept all APIs as fallback

        for i, dev in enumerate(all_devs):
            try:
                if not isinstance(dev, dict):
                    continue
                if dev.get("max_input_channels", 0) <= 0:
                    continue  # Output-only device

                if api_indices is not None:
                    if dev.get("hostapi", -1) not in api_indices:
                        continue

                name = dev.get("name", f"Device {i}")
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                name = str(name)[:50]

                if name in seen_names:
                    continue
                seen_names.add(name)

                devices.append((str(i), name))

                if len(devices) >= 25:
                    break
            except Exception:
                continue

    except Exception as e:
        logger.warning("Could not list input devices", error=str(e))

    return devices


class MicCapture:
    """
    Captures audio from a real microphone using sounddevice.InputStream.

    The sounddevice audio callback runs in a C-level thread; chunks are placed
    into a thread-safe queue and drained by an asyncio consumer task — the same
    pattern used by ``AudioCapture`` for loopback capture.

    Usage::

        capture = MicCapture()
        await capture.start(device_id="3", on_audio_chunk=my_callback)
        # ... later ...
        await capture.stop()
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size_ms: int = 100,
    ) -> None:
        self._sample_rate = sample_rate
        self._chunk_size_ms = chunk_size_ms
        # Number of frames per chunk
        self._chunk_frames = int(sample_rate * chunk_size_ms / 1000)

        self._is_capturing = threading.Event()
        self._audio_queue: queue.Queue[tuple[bytes, int]] = queue.Queue(maxsize=100)
        self._callback: AudioCallback | None = None
        self._device_index: int | None = None
        self._consumer_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._stream: sd.InputStream | None = None

    # ── Public API ────────────────────────────────────────────────────────

    async def start(
        self,
        device_id: str | None = None,
        on_audio_chunk: AudioCallback | None = None,
    ) -> None:
        """Start capturing from the selected microphone.

        Args:
            device_id: sounddevice device index as a string, or ``None`` / ``""``
                       for the system default input device.
            on_audio_chunk: Async callback ``(audio_bytes: bytes, timestamp_ms: int)``
                            invoked for every captured chunk (float32 mono).

        Raises:
            RuntimeError: If the device cannot be opened.
        """
        if self._is_capturing.is_set():
            logger.warning("Mic capture already running")
            return

        self._callback = on_audio_chunk
        self._device_index = int(device_id) if device_id else None

        logger.info(
            "Starting mic capture",
            device=device_id,
            sample_rate=self._sample_rate,
            chunk_ms=self._chunk_size_ms,
        )

        self._is_capturing.set()

        try:
            self._stream = sd.InputStream(
                device=self._device_index,
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._chunk_frames,
                latency="low",
                callback=self._sd_callback,
            )
            self._stream.start()
            logger.info("Mic stream opened", device_index=self._device_index)
        except Exception as e:
            self._is_capturing.clear()
            raise RuntimeError(f"Failed to open microphone (device {device_id}): {e}") from e

        self._consumer_task = asyncio.create_task(self._consume_audio())

    async def stop(self) -> None:
        """Stop capturing and release the device."""
        if not self._is_capturing.is_set():
            return

        logger.info("Stopping mic capture")
        self._is_capturing.clear()

        # Cancel consumer task
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            self._consumer_task = None

        # Close sounddevice stream
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug("Error closing mic stream", error=str(e))
            self._stream = None

        # Drain queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    @property
    def is_capturing(self) -> bool:
        """Return True while capture is active."""
        return self._is_capturing.is_set()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    # ── Private helpers ──────────────────────────────────────────────────

    def _sd_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """sounddevice audio callback — runs in a C-level audio thread.

        Converts the incoming block to mono float32 bytes and pushes it onto
        the thread-safe queue for the async consumer to drain.
        """
        if not self._is_capturing.is_set():
            return

        if status:
            logger.debug("Mic stream status flags", status=str(status))

        # indata shape: (frames, channels) — take first channel as mono
        mono: np.ndarray = indata[:, 0].copy()
        audio_bytes = mono.astype(np.float32).tobytes()
        timestamp_ms = int(time.time() * 1000)

        try:
            self._audio_queue.put_nowait((audio_bytes, timestamp_ms))
        except queue.Full:
            # Drop oldest and enqueue newest to stay current
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait((audio_bytes, timestamp_ms))
            except queue.Empty:
                pass

    async def _consume_audio(self) -> None:
        """Async consumer — drains the queue and forwards chunks to the callback."""
        while self._is_capturing.is_set():
            try:
                try:
                    audio_bytes, timestamp_ms = self._audio_queue.get_nowait()
                    if self._callback:
                        await self._callback(audio_bytes, timestamp_ms)
                except queue.Empty:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in mic audio consumer", error=str(e))
                await asyncio.sleep(0.1)
