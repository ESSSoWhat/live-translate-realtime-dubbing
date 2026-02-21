"""
Voice Activity Detection using Silero VAD.
"""

import contextlib
from dataclasses import dataclass

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class VADResult:
    """Result from VAD processing."""

    is_speech: bool
    confidence: float
    speech_start_ms: int | None = None
    speech_end_ms: int | None = None


@dataclass
class SpeechSegment:
    """A detected speech segment."""

    start_ms: int
    end_ms: int
    audio_data: np.ndarray
    confidence: float


class SileroVAD:
    """
    Voice Activity Detection using Silero VAD model.

    Silero VAD is a lightweight, fast, and accurate VAD model
    that runs in <1ms per audio chunk on CPU.
    """

    def __init__(
        self,
        threshold: float = 0.15,  # Sensitive so voice capture and STT trigger
        sample_rate: int = 16000,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 100,
    ) -> None:
        """
        Initialize Silero VAD.

        Args:
            threshold: Speech detection threshold (0.0 to 1.0)
            sample_rate: Audio sample rate (8000 or 16000)
            min_speech_duration_ms: Minimum speech duration to detect
            min_silence_duration_ms: Minimum silence to end speech
        """
        self._threshold = threshold
        self._sample_rate = sample_rate
        self._min_speech_duration_ms = min_speech_duration_ms
        self._min_silence_duration_ms = min_silence_duration_ms

        # Model state
        self._model = None
        self._is_loaded = False

        # Speech tracking state
        self._is_speaking = False
        self._speech_start_ms: int | None = None
        self._silence_start_ms: int | None = None
        self._current_time_ms = 0

    def load_model(self) -> None:
        """Load the Silero VAD model.

        Uses the locally-bundled ``silero_vad`` package (JIT model file)
        instead of ``torch.hub.load`` which tries to download from GitHub
        and fails in bundled / offline environments.
        """
        if self._is_loaded:
            return

        try:
            from silero_vad import load_silero_vad

            model = load_silero_vad(onnx=False)

            self._model = model
            self._is_loaded = True
            logger.info("Silero VAD model loaded (local)")

        except Exception as e:
            logger.exception("Failed to load Silero VAD", error=str(e))
            raise

    def process_chunk(
        self, audio: np.ndarray, timestamp_ms: int | None = None
    ) -> VADResult:
        """
        Process an audio chunk and detect speech.

        Args:
            audio: Audio data as numpy array (float32, mono)
            timestamp_ms: Optional timestamp for the chunk

        Returns:
            VADResult with speech detection info
        """
        if not self._is_loaded:
            self.load_model()

        # Update timestamp
        if timestamp_ms is not None:
            self._current_time_ms = timestamp_ms
        else:
            chunk_duration_ms = len(audio) / self._sample_rate * 1000
            self._current_time_ms += int(chunk_duration_ms)

        try:
            import torch

            # Silero VAD requires exactly 512 samples for 16kHz (or 256 for 8kHz)
            # Process larger chunks by splitting into 512-sample windows
            chunk_size = 512 if self._sample_rate == 16000 else 256

            # If audio is smaller than chunk_size, pad it
            if len(audio) < chunk_size:
                audio = np.pad(audio, (0, chunk_size - len(audio)))

            # Process in 512-sample chunks and take max speech probability
            speech_probs: list[float] = []
            for i in range(0, len(audio) - chunk_size + 1, chunk_size):
                chunk = audio[i:i + chunk_size]

                # Convert to torch tensor
                audio_copy = np.array(chunk, dtype=np.float32, copy=True)
                audio_tensor = torch.from_numpy(audio_copy)

                # Ensure correct shape [1, num_samples]
                if audio_tensor.dim() == 1:
                    audio_tensor = audio_tensor.unsqueeze(0)

                # Run VAD on this chunk
                with torch.no_grad():
                    if self._model is None:
                        raise RuntimeError("VAD model not loaded")
                    prob = self._model(audio_tensor, self._sample_rate).item()
                    speech_probs.append(prob)

            # Use max probability across all chunks (if any chunk has speech, consider it speech)
            speech_prob = max(speech_probs) if speech_probs else 0.0
            is_speech = speech_prob >= self._threshold

            # Debug logging for first few chunks and periodically (use info for visibility)
            if not hasattr(self, '_debug_count'):
                self._debug_count = 0
            self._debug_count += 1
            if self._debug_count <= 5 or self._debug_count % 100 == 0:
                audio_level = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
                logger.info(
                    "VAD chunk processed",
                    speech_prob=f"{speech_prob:.3f}",
                    threshold=self._threshold,
                    is_speech=is_speech,
                    audio_level=f"{audio_level:.4f}",
                    audio_len=len(audio),
                )

            # Track speech segments
            result = self._update_speech_tracking(
                is_speech, speech_prob, audio
            )

            return result

        except Exception as e:
            # Use logger.error instead of exception to avoid encoding issues with traceback
            logger.error("VAD processing error", error=repr(e))
            return VADResult(is_speech=False, confidence=0.0)

    def _update_speech_tracking(
        self, is_speech: bool, confidence: float, audio: np.ndarray
    ) -> VADResult:
        """Update speech segment tracking state."""
        result = VADResult(is_speech=is_speech, confidence=confidence)

        if is_speech:
            self._silence_start_ms = None

            if not self._is_speaking:
                # Speech started
                self._is_speaking = True
                self._speech_start_ms = self._current_time_ms
                result.speech_start_ms = self._speech_start_ms

        else:
            if self._is_speaking:
                if self._silence_start_ms is None:
                    self._silence_start_ms = self._current_time_ms

                silence_duration = self._current_time_ms - self._silence_start_ms

                if silence_duration >= self._min_silence_duration_ms:
                    # Speech ended
                    speech_duration = (
                        self._silence_start_ms - self._speech_start_ms
                        if self._speech_start_ms is not None
                        else 0
                    )

                    if speech_duration >= self._min_speech_duration_ms:
                        result.speech_end_ms = self._silence_start_ms

                    self._is_speaking = False
                    self._speech_start_ms = None

        return result

    def get_speech_segments(
        self, audio: np.ndarray, chunk_size_ms: int = 100
    ) -> list[SpeechSegment]:
        """
        Process full audio and return speech segments.

        Args:
            audio: Full audio data
            chunk_size_ms: Size of chunks to process

        Returns:
            List of detected speech segments
        """
        segments: list[SpeechSegment] = []

        # Calculate chunk size in samples
        chunk_samples = int(self._sample_rate * chunk_size_ms / 1000)

        # Process in chunks
        current_segment_start: int | None = None
        current_segment_audio: list[np.ndarray] = []

        for i in range(0, len(audio), chunk_samples):
            chunk = audio[i:i + chunk_samples]
            if len(chunk) < chunk_samples:
                # Pad last chunk
                chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))

            timestamp_ms = int(i / self._sample_rate * 1000)
            result = self.process_chunk(chunk, timestamp_ms)

            if result.speech_start_ms is not None:
                current_segment_start = result.speech_start_ms
                current_segment_audio = [chunk]

            elif result.is_speech and current_segment_start is not None:
                current_segment_audio.append(chunk)

            elif result.speech_end_ms is not None and current_segment_start is not None:
                # Segment ended
                segment = SpeechSegment(
                    start_ms=current_segment_start,
                    end_ms=result.speech_end_ms,
                    audio_data=np.concatenate(current_segment_audio),
                    confidence=result.confidence,
                )
                segments.append(segment)
                current_segment_start = None
                current_segment_audio = []

        # Handle any remaining segment
        if current_segment_start is not None and current_segment_audio:
            segment = SpeechSegment(
                start_ms=current_segment_start,
                end_ms=self._current_time_ms,
                audio_data=np.concatenate(current_segment_audio),
                confidence=0.5,
            )
            segments.append(segment)

        return segments

    def reset_state(self) -> None:
        """Reset VAD tracking state."""
        self._is_speaking = False
        self._speech_start_ms = None
        self._silence_start_ms = None
        self._current_time_ms = 0

        # Reset model state if needed
        if self._model is not None:
            with contextlib.suppress(Exception):
                self._model.reset_states()

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._is_loaded

    @property
    def is_speaking(self) -> bool:
        """Check if currently detecting speech."""
        return self._is_speaking

    @property
    def threshold(self) -> float:
        """Get speech detection threshold."""
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        """Set speech detection threshold."""
        self._threshold = max(0.0, min(1.0, value))
