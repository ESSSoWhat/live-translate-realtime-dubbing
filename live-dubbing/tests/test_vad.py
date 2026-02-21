"""
Tests for Voice Activity Detection module.
"""
# mypy: disable-error-code="import-untyped"

import pytest
import numpy as np

# Skip tests if torch is not available
pytest.importorskip("torch")


class TestSileroVAD:
    """Tests for Silero VAD wrapper."""

    def test_vad_initialization(self):
        """VAD initializes with correct parameters."""
        from live_dubbing.processing.vad import SileroVAD

        vad = SileroVAD(threshold=0.6, sample_rate=16000)
        assert vad.threshold == 0.6
        assert not vad.is_loaded
        assert not vad.is_speaking

    def test_vad_threshold_setter(self):
        """VAD threshold can be updated."""
        from live_dubbing.processing.vad import SileroVAD

        vad = SileroVAD(threshold=0.5)
        vad.threshold = 0.8
        assert vad.threshold == 0.8

        # Test clamping
        vad.threshold = 1.5
        assert vad.threshold == 1.0

        vad.threshold = -0.5
        assert vad.threshold == 0.0

    def test_vad_result_dataclass(self):
        """Assert that VADResult dataclass works correctly."""
        from live_dubbing.processing.vad import VADResult

        result = VADResult(
            is_speech=True,
            confidence=0.85,
            speech_start_ms=1000,
        )
        assert result.is_speech
        assert result.confidence == 0.85
        assert result.speech_start_ms == 1000
        assert result.speech_end_ms is None

    def test_speech_segment_dataclass(self):
        """Assert that SpeechSegment dataclass works correctly."""
        from live_dubbing.processing.vad import SpeechSegment

        audio = np.zeros(1600, dtype=np.float32)
        segment = SpeechSegment(
            start_ms=0,
            end_ms=100,
            audio_data=audio,
            confidence=0.9,
        )
        assert segment.start_ms == 0
        assert segment.end_ms == 100
        assert len(segment.audio_data) == 1600
        assert segment.confidence == 0.9

    def test_vad_reset_state(self):
        """VAD state can be reset."""
        from live_dubbing.processing.vad import SileroVAD

        vad = SileroVAD()
        vad._is_speaking = True
        vad._speech_start_ms = 1000
        vad._current_time_ms = 5000

        vad.reset_state()

        assert not vad._is_speaking
        assert vad._speech_start_ms is None
        assert vad._current_time_ms == 0


class TestVADWithMock:
    """Tests for VAD with mocked model."""

    def test_process_silence(self, sample_silence):
        """VAD correctly identifies silence."""
        from live_dubbing.processing.vad import SileroVAD

        vad = SileroVAD(threshold=0.5)

        # Mock the model to return low confidence
        class MockModel:
            """Minimal VAD model mock returning low speech probability."""

            def __call__(self, audio, sr):
                """Return low confidence (no speech)."""
                return 0.1  # Low confidence = no speech

            def reset_states(self):
                """No-op for mock."""

        vad._model = MockModel()
        vad._is_loaded = True

        result = vad.process_chunk(sample_silence)
        assert not result.is_speech
        assert result.confidence < 0.5

    def test_process_speech(self, sample_speech_audio):
        """VAD correctly identifies speech."""
        from live_dubbing.processing.vad import SileroVAD

        vad = SileroVAD(threshold=0.5)

        # Mock the model to return high confidence (VAD calls .item() on result)
        class MockTensor:
            """Tensor-like object returning fixed value for .item()."""

            def item(self):
                """Return high speech probability."""
                return 0.9

        class MockModel:
            """Minimal VAD model mock returning high speech probability."""

            def __call__(self, audio, sr):
                """Return high confidence (speech)."""
                return MockTensor()  # High confidence = speech

            def reset_states(self):
                """No-op for mock."""

        vad._model = MockModel()
        vad._is_loaded = True

        result = vad.process_chunk(sample_speech_audio)
        assert result.is_speech
        assert result.confidence >= 0.5
