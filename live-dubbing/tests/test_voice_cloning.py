"""Tests for dynamic voice-clone capture gating."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from live_dubbing.processing.speaker_id import SpeakerIdentifier
from live_dubbing.services.voice_cloning import ClonedVoice, VoiceCloneManager
from live_dubbing.services.voice_profiles import VoiceProfileStore


class _FakeElevenLabs:
    """Minimal stand-in; clone_voice should not be called in these tests."""

    async def clone_voice(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("clone_voice should not run for short samples")


def _manager(min_sec: float = 5.0, tmp_path: Path | None = None) -> VoiceCloneManager:
    store = None
    if tmp_path is not None:
        store = VoiceProfileStore(path=str(tmp_path / "voice_profiles.json"))
    return VoiceCloneManager(
        _FakeElevenLabs(),
        min_sample_duration_sec=min_sec,
        profile_store=store,
    )


def _tone(freq_hz: float, seconds: float = 1.0, sr: int = 16000) -> np.ndarray:
    t = np.arange(int(sr * seconds), dtype=np.float32) / float(sr)
    return (0.2 * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float32)


def test_add_audio_chunk_ready_only_once() -> None:
    """Crossing the min duration emits ready once so we do not double-submit."""
    mgr = _manager(min_sec=0.2)
    chunk = np.full(1600, 0.1, dtype=np.float32)  # 0.1s at 16 kHz, speech-like level
    mgr._is_capturing = True
    mgr._sample_rate = 16000
    assert mgr.add_audio_chunk(chunk) is False
    assert mgr.add_audio_chunk(chunk) is True
    assert mgr.add_audio_chunk(chunk) is False


def test_add_audio_chunk_skips_clipped_and_silent() -> None:
    """Game SFX (clipping) and near-silence must not fill the clone buffer."""
    mgr = _manager(min_sec=0.2)
    mgr._is_capturing = True
    mgr._sample_rate = 16000
    clipped = np.full(1600, 0.99, dtype=np.float32)
    silent = np.zeros(1600, dtype=np.float32)
    assert mgr.add_audio_chunk(clipped) is False
    assert mgr.add_audio_chunk(silent) is False
    assert mgr._buffer_duration_sec == 0.0


@pytest.mark.asyncio
async def test_create_dynamic_clone_rejects_short_buffer() -> None:
    """Wall-clock overlay finishes must not send a too-short clip to ElevenLabs."""
    mgr = _manager(min_sec=5.0)
    mgr._is_capturing = True
    mgr._audio_buffer = [np.zeros(1600, dtype=np.float32)]  # 0.1s
    mgr._buffer_duration_sec = 0.1
    with pytest.raises(RuntimeError, match="Not enough speech"):
        await mgr.create_dynamic_clone(name="Clone 15:28")


def test_unmatched_voices_each_get_a_profile(tmp_path: Path) -> None:
    """Distinct unmatched speakers must not collapse onto two shared profiles."""
    mgr = _manager(tmp_path=tmp_path)
    mgr._speaker_id.identify = lambda _audio: (None, 0.0)  # type: ignore[method-assign]
    ids: list[str] = []
    for seed in range(12):
        rng = np.random.RandomState(seed)
        audio = (rng.randn(16000).astype(np.float32) * 0.1)
        profile, _confidence = mgr.resolve_profile_for_audio(audio)
        assert profile is not None
        ids.append(profile.id)
    assert len(set(ids)) == 12
    assert len(mgr.list_profiles()) == 12


def test_distinct_tones_are_separate_speakers() -> None:
    """MFCC matching must not treat clearly different voices as the same speaker."""
    sid = SpeakerIdentifier(sample_rate=16000)
    sid.register_speaker("low", _tone(180.0))
    match_id, score = sid.identify(_tone(180.0))
    assert match_id == "low"
    assert score >= 0.78
    other_id, other_score = sid.identify(_tone(1400.0))
    assert other_id is None
    assert other_score < 0.78


def test_feed_profile_clone_ready_once(tmp_path: Path) -> None:
    """Per-speaker clone capture should signal ready only once."""
    mgr = _manager(min_sec=0.2, tmp_path=tmp_path)
    mgr._speaker_id.identify = lambda _audio: (None, 0.0)  # type: ignore[method-assign]
    profile, _ = mgr.resolve_profile_for_audio(_tone(220.0))
    assert profile is not None
    chunk = np.full(1600, 0.1, dtype=np.float32)
    assert mgr.feed_profile_clone_audio(profile.id, chunk) is False
    assert mgr.feed_profile_clone_audio(profile.id, chunk) is True
    assert mgr.feed_profile_clone_audio(profile.id, chunk) is False


def test_matched_speaker_uses_assigned_clone(tmp_path: Path) -> None:
    """A speaker that matches an existing clone must reuse that voice."""
    mgr = _manager(tmp_path=tmp_path)
    profile, _ = mgr.resolve_profile_for_audio(_tone(180.0))
    assert profile is not None
    clone = ClonedVoice(voice_id="voice-low", name="Low", is_dynamic=True)
    mgr._voice_cache[clone.voice_id] = clone
    assert mgr.set_profile_voice(profile.id, clone.voice_id)

    matched, _ = mgr.resolve_profile_for_audio(_tone(180.0))
    resolved = mgr.resolve_tts_voice(matched, _tone(180.0))
    assert matched is not None
    assert matched.id == profile.id
    assert resolved is not None
    assert resolved.voice_id == "voice-low"


def test_unmatched_speaker_keeps_clone_unassigned(tmp_path: Path) -> None:
    """No speaker match: use closest clone for TTS, then clone this speaker."""
    mgr = _manager(min_sec=0.2, tmp_path=tmp_path)
    low, _ = mgr.resolve_profile_for_audio(_tone(180.0))
    assert low is not None
    clone = ClonedVoice(voice_id="voice-low", name="Low", is_dynamic=True)
    mgr._voice_cache[clone.voice_id] = clone
    assert mgr.set_profile_voice(low.id, clone.voice_id)

    high, _ = mgr.resolve_profile_for_audio(_tone(1400.0))
    assert high is not None
    assert high.id != low.id
    assert high.assigned_voice_id is None

    interim = mgr.resolve_tts_voice(high, _tone(1400.0))
    assert interim is not None
    assert interim.voice_id == "voice-low"
    assert high.assigned_voice_id is None

    chunk = np.full(1600, 0.1, dtype=np.float32)
    assert mgr.feed_profile_clone_audio(high.id, chunk) is False
    assert mgr.feed_profile_clone_audio(high.id, chunk) is True


def test_close_match_assigns_existing_clone(tmp_path: Path) -> None:
    """Above-threshold closest clone is persisted onto the unmatched profile."""
    mgr = _manager(tmp_path=tmp_path)
    source, _ = mgr.resolve_profile_for_audio(_tone(180.0))
    assert source is not None
    clone = ClonedVoice(voice_id="voice-a", name="A", is_dynamic=True)
    mgr._voice_cache[clone.voice_id] = clone
    assert mgr.set_profile_voice(source.id, clone.voice_id)

    mgr._speaker_id.identify = lambda _audio: (None, 0.0)  # type: ignore[method-assign]
    target, _ = mgr.resolve_profile_for_audio(_tone(200.0))
    assert target is not None
    mgr._speaker_id.ranked_matches = lambda _audio: [  # type: ignore[method-assign]
        (source.id, 0.90)
    ]

    resolved = mgr.resolve_tts_voice(target, _tone(200.0))
    assert resolved is not None
    assert resolved.voice_id == "voice-a"
    assert target.assigned_voice_id == "voice-a"
