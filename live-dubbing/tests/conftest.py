"""
Pytest configuration and fixtures.
"""

# Import torch before PyQt6 to avoid DLL loading conflict on Windows.
# When Qt DLLs are loaded first, torch's c10.dll fails with access violation.
try:
    import torch  # noqa: F401
except ImportError:
    pass

import pytest
import numpy as np
from unittest.mock import Mock, AsyncMock


@pytest.fixture
def mock_elevenlabs_service():
    """Create a mock ElevenLabs service."""
    service = Mock()
    service.clone_voice = AsyncMock(return_value="test_voice_id")
    service.transcribe = AsyncMock(return_value=Mock(
        text="Hello world",
        language_code="en",
        confidence=0.95,
    ))
    service.synthesize = AsyncMock(return_value=b"audio_data")
    service.list_voices = AsyncMock(return_value=[])
    return service


@pytest.fixture
def sample_audio_chunk():
    """Generate sample audio data."""
    # 100ms of audio at 16kHz
    duration_sec = 0.1
    sample_rate = 16000
    samples = int(duration_sec * sample_rate)

    # Generate simple sine wave
    t = np.linspace(0, duration_sec, samples, dtype=np.float32)
    frequency = 440  # A4 note
    audio = 0.5 * np.sin(2 * np.pi * frequency * t)

    return audio


@pytest.fixture
def sample_speech_audio():
    """Generate sample speech-like audio data."""
    # 1 second of audio at 16kHz with varying amplitude
    duration_sec = 1.0
    sample_rate = 16000
    samples = int(duration_sec * sample_rate)

    t = np.linspace(0, duration_sec, samples, dtype=np.float32)

    # Mix of frequencies to simulate speech
    audio = (
        0.3 * np.sin(2 * np.pi * 150 * t) +  # Low
        0.4 * np.sin(2 * np.pi * 300 * t) +  # Mid
        0.2 * np.sin(2 * np.pi * 600 * t)    # High
    )

    # Add envelope to simulate speech patterns
    envelope = np.abs(np.sin(2 * np.pi * 3 * t))
    audio = (audio * envelope).astype(np.float32)

    return audio


@pytest.fixture
def sample_silence():
    """Generate silence."""
    duration_sec = 0.5
    sample_rate = 16000
    samples = int(duration_sec * sample_rate)
    return np.zeros(samples, dtype=np.float32)


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus."""
    bus = Mock()
    bus.subscribe = Mock(return_value=Mock())
    bus.emit = Mock()
    bus.emit_error = Mock()
    bus.emit_warning = Mock()
    return bus


@pytest.fixture
def mock_settings():
    """Create mock app settings."""
    from live_dubbing.config.settings import AppSettings
    return AppSettings()
