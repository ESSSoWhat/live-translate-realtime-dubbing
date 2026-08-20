"""Tests for dynamic voice-clone capture gating."""

from __future__ import annotations

import numpy as np
import pytest

from live_dubbing.services.voice_cloning import VoiceCloneManager


class _FakeElevenLabs:
    """Minimal stand-in; clone_voice should not be called in these tests."""

    async def clone_voice(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("clone_voice should not run for short samples")


def _manager(min_sec: float = 5.0) -> VoiceCloneManager:
    return VoiceCloneManager(_FakeElevenLabs(), min_sample_duration_sec=min_sec)


def test_add_audio_chunk_ready_only_once() -> None:
    """Crossing the min duration emits ready once so we do not double-submit."""
    mgr = _manager(min_sec=0.2)
    chunk = np.zeros(1600, dtype=np.float32)  # 0.1s at 16 kHz
    mgr._is_capturing = True
    mgr._sample_rate = 16000
    assert mgr.add_audio_chunk(chunk) is False
    assert mgr.add_audio_chunk(chunk) is True
    assert mgr.add_audio_chunk(chunk) is False


@pytest.mark.asyncio
async def test_create_dynamic_clone_rejects_short_buffer() -> None:
    """Wall-clock overlay finishes must not send a too-short clip to ElevenLabs."""
    mgr = _manager(min_sec=5.0)
    mgr._is_capturing = True
    mgr._audio_buffer = [np.zeros(1600, dtype=np.float32)]  # 0.1s
    mgr._buffer_duration_sec = 0.1
    with pytest.raises(RuntimeError, match="Not enough speech"):
        await mgr.create_dynamic_clone(name="Clone 15:28")
