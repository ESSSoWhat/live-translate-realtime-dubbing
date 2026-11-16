"""Translation language handling and session voice seeding."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from live_dubbing.core.events import EventBus
from live_dubbing.processing.pipeline import PipelineConfig, ProcessingPipeline
from live_dubbing.services.voice_cloning import ClonedVoice, VoiceCloneManager
from live_dubbing.services.voice_profiles import VoiceProfileStore


@pytest.mark.asyncio
async def test_translate_passes_source_and_context() -> None:
    service = Mock()
    service.translate_text = AsyncMock(return_value="hola")
    pipeline = ProcessingPipeline(
        service,
        EventBus(),
        PipelineConfig(target_language="es", preserve_context=True),
    )
    pipeline._translation_context = ["Earlier line."]

    result = await pipeline._translate_for_tts("Hello there", "en-US")

    assert result == "hola"
    kwargs = service.translate_text.await_args.kwargs
    assert service.translate_text.await_args.args[0] == "Hello there"
    assert service.translate_text.await_args.args[1] == "es"
    assert kwargs["source_language"] == "en"
    assert "Earlier line." in kwargs["context"]
    assert pipeline._translation_context[-1] == "Hello there"


@pytest.mark.asyncio
async def test_same_base_language_skips_translation() -> None:
    service = Mock()
    service.translate_text = AsyncMock(return_value="should-not-run")
    pipeline = ProcessingPipeline(
        service,
        EventBus(),
        PipelineConfig(target_language="en"),
    )

    result = await pipeline._translate_for_tts("Already English", "en-GB")

    assert result == "Already English"
    service.translate_text.assert_not_awaited()


def test_session_starts_on_preferred_saved_voice(tmp_path) -> None:
    service = Mock()
    pipeline = ProcessingPipeline(
        service,
        EventBus(),
        PipelineConfig(
            auto_clone_voice=True,
            preferred_voice_id="saved-voice",
            min_voice_capture_sec=0.5,
        ),
    )
    pipeline._voice_manager = VoiceCloneManager(
        service,
        min_sample_duration_sec=0.5,
        profile_store=VoiceProfileStore(str(tmp_path / "profiles.json")),
    )
    pipeline._voice_manager._voice_cache["saved-voice"] = ClonedVoice(
        voice_id="saved-voice",
        name="Saved speaker",
        is_dynamic=True,
    )

    seeded = pipeline._initial_session_voice()
    assert seeded.voice_id == "saved-voice"
    assert seeded.name == "Saved speaker"
