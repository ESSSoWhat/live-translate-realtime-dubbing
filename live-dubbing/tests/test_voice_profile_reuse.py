"""Similar clone audio joins the existing profile instead of creating another."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from live_dubbing.services.voice_cloning import VoiceCloneManager
from live_dubbing.services.voice_profiles import VoiceProfileStore


def _tone(freq: float, seconds: float = 1.0, rate: int = 16000) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False, dtype=np.float32)
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _manager(tmp_path, clone_id: str = "voice-1") -> tuple[VoiceCloneManager, Mock]:
    service = Mock()
    service.clone_voice = AsyncMock(return_value=clone_id)
    service.clone_voice_from_file = AsyncMock(return_value=clone_id)
    store = VoiceProfileStore(str(tmp_path / "profiles.json"))
    manager = VoiceCloneManager(
        service,
        min_sample_duration_sec=0.5,
        profile_store=store,
    )
    return manager, service


async def _capture(manager: VoiceCloneManager, audio: np.ndarray, name: str):
    await manager.start_dynamic_capture()
    manager.add_audio_chunk(audio)
    return await manager.create_dynamic_clone(name=name)


@pytest.mark.asyncio
async def test_similar_capture_reuses_the_existing_profile(tmp_path) -> None:
    manager, service = _manager(tmp_path)
    tone = _tone(180)

    first = await _capture(manager, tone, "first")
    assert first.voice_id == "voice-1"
    assert first.reused_existing is False
    assert service.clone_voice.await_count == 1
    assert len(manager.list_profiles()) == 1

    service.clone_voice = AsyncMock(return_value="voice-should-not-be-created")
    second = await _capture(manager, tone.copy(), "second")

    assert second.reused_existing is True
    assert second.voice_id == "voice-1"
    assert service.clone_voice.await_count == 0
    assert len(manager.list_profiles()) == 1
    profile = manager.list_profiles()[0]
    assert profile.assigned_voice_id == "voice-1"
    assert profile.embedding_samples == 2
    payload = manager.clone_event_data(second)
    assert payload["reused"] is True
    assert payload["profile_name"] == profile.name
    assert payload["name"] == profile.name

    reloaded = VoiceProfileStore(str(tmp_path / "profiles.json")).load_all()
    assert reloaded[0].embedding_samples == 2
    assert reloaded[0].assigned_voice_id == "voice-1"


@pytest.mark.asyncio
async def test_similar_profile_without_a_voice_keeps_one_profile(tmp_path) -> None:
    manager, service = _manager(tmp_path, "voice-1")
    tone = _tone(220)
    await _capture(manager, tone, "first")
    profile = manager.list_profiles()[0]
    assert manager.set_profile_voice(profile.id, None)

    service.clone_voice = AsyncMock(return_value="voice-2")
    second = await _capture(manager, tone.copy(), "second")

    assert second.reused_existing is False
    assert second.voice_id == "voice-2"
    assert service.clone_voice.await_count == 1
    profiles = manager.list_profiles()
    assert len(profiles) == 1
    assert profiles[0].id == profile.id
    assert profiles[0].assigned_voice_id == "voice-2"
    assert profiles[0].embedding_samples == 2


@pytest.mark.asyncio
async def test_different_speaker_gets_its_own_profile(tmp_path) -> None:
    manager, service = _manager(tmp_path, "voice-1")
    await _capture(manager, _tone(140), "low")

    service.clone_voice = AsyncMock(return_value="voice-2")
    noise = np.random.default_rng(0).uniform(-0.5, 0.5, 16000).astype(np.float32)
    second = await _capture(manager, noise, "noise")

    assert second.reused_existing is False
    assert second.voice_id == "voice-2"
    assert len(manager.list_profiles()) == 2


@pytest.mark.asyncio
async def test_each_line_is_cloned_from_its_own_audio(tmp_path) -> None:
    manager, service = _manager(tmp_path, "voice-1")
    service.clone_voice = AsyncMock(side_effect=["voice-a", "voice-b"])
    tone = _tone(180, seconds=1.2)
    noise = np.random.default_rng(1).uniform(-0.5, 0.5, 16000).astype(np.float32)

    first = await manager.voice_for_utterance(tone)
    second = await manager.voice_for_utterance(noise)

    assert first is not None and first.voice_id == "voice-a"
    assert second is not None and second.voice_id == "voice-b"
    assert service.clone_voice.await_count == 2
    first_wav = service.clone_voice.await_args_list[0].kwargs["audio_data"]
    import io
    import wave

    with wave.open(io.BytesIO(first_wav), "rb") as wav_file:
        assert wav_file.getnframes() == len(tone)

    again = await manager.voice_for_utterance(tone.copy())
    assert again is not None
    assert again.reused_existing is True
    assert again.voice_id == "voice-a"
    assert service.clone_voice.await_count == 2


@pytest.mark.asyncio
async def test_translated_line_keeps_its_voice_after_the_next_line(tmp_path) -> None:
    from live_dubbing.core.events import EventBus
    from live_dubbing.processing.pipeline import PipelineConfig, ProcessingPipeline
    from live_dubbing.services.voice_cloning import ClonedVoice

    service = Mock()
    service.clone_voice = AsyncMock(side_effect=["voice-a", "voice-b"])
    pipeline = ProcessingPipeline(
        service,
        EventBus(),
        PipelineConfig(auto_clone_voice=True, min_voice_capture_sec=0.5),
    )
    pipeline._voice_manager = VoiceCloneManager(
        service,
        min_sample_duration_sec=0.5,
        profile_store=VoiceProfileStore(str(tmp_path / "profiles.json")),
    )
    pipeline._cloned_voice = ClonedVoice(voice_id="fallback", name="Fallback")

    first_task = asyncio.create_task(pipeline._voice_for_translated_line(_tone(180)))
    first_item = await pipeline._item_for_line("first line", first_task)
    noise = np.random.default_rng(2).uniform(-0.5, 0.5, 16000).astype(np.float32)
    second_task = asyncio.create_task(pipeline._voice_for_translated_line(noise))
    second_item = await pipeline._item_for_line("second line", second_task)

    assert first_item.voice is not None and first_item.voice.voice_id == "voice-a"
    assert second_item.voice is not None and second_item.voice.voice_id == "voice-b"
    assert pipeline._cloned_voice is not None
    assert pipeline._cloned_voice.voice_id == "voice-b"
    assert first_item.text == "first line"
