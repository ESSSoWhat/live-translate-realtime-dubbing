"""
Proxy endpoints — forward requests to ElevenLabs/OpenAI after quota checks.
API keys live only here on the server and are never sent to the desktop app.
"""

from __future__ import annotations

import asyncio
import io
import os

import httpx
import structlog
from elevenlabs import AsyncElevenLabs
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.requests import CloneVoiceRequest, SynthesizeRequest, TranslateRequest
from app.models.responses import (
    CloneVoiceResponse,
    TranscriptionResponse,
    TranslationResponse,
    VoiceItem,
)
from app.services.usage import QuotaExceededError, check_quota, record_usage

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])

UPGRADE_URL = "https://livetranslate.app/upgrade"  # TODO: replace with real URL


def _elevenlabs() -> AsyncElevenLabs:
    cfg = get_settings()
    return AsyncElevenLabs(api_key=cfg.elevenlabs_api_key, timeout=60.0)


def _quota_error(exc: QuotaExceededError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error": "quota_exceeded",
            "event_type": exc.event_type,
            "used": exc.used,
            "limit": exc.limit,
            "upgrade_url": UPGRADE_URL,
        },
    )


# ── STT ─────────────────────────────────────────────────────────────────────

@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    language: str = Form(default="auto"),
    user: dict = Depends(get_current_user),
) -> TranscriptionResponse:
    audio_bytes = await audio.read()
    duration_seconds = max(1, len(audio_bytes) // 32000)  # rough estimate at 16kHz/16bit

    try:
        await check_quota(user["id"], "stt", duration_seconds)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc

    client = _elevenlabs()
    try:
        result = await asyncio.to_thread(
            client._client.speech_to_text.convert,  # type: ignore[attr-defined]
            audio=io.BytesIO(audio_bytes),
            model_id="scribe_v1",
            language_code=None if language == "auto" else language,
        )
    except Exception as exc:
        logger.error("ElevenLabs STT error", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc

    background_tasks.add_task(record_usage, user["id"], "stt", duration_seconds)

    return TranscriptionResponse(
        text=result.text,
        language_code=result.language_code or language,
    )


# ── TTS ─────────────────────────────────────────────────────────────────────

@router.post("/synthesize")
async def synthesize(
    body: SynthesizeRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    char_count = len(body.text)

    try:
        await check_quota(user["id"], "tts", char_count)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc

    client = _elevenlabs()
    try:
        audio_bytes = await asyncio.to_thread(
            client._client.text_to_speech.convert,  # type: ignore[attr-defined]
            voice_id=body.voice_id,
            text=body.text,
            model_id=body.model_id,
            voice_settings={"stability": body.stability, "similarity_boost": body.similarity_boost},
            output_format="mp3_44100_128",
        )
        audio_data = b"".join(audio_bytes) if hasattr(audio_bytes, "__iter__") else audio_bytes
    except Exception as exc:
        logger.error("ElevenLabs TTS error", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Synthesis failed: {exc}") from exc

    background_tasks.add_task(record_usage, user["id"], "tts", char_count)

    return StreamingResponse(io.BytesIO(audio_data), media_type="audio/mpeg")


@router.post("/synthesize/stream")
async def synthesize_stream(
    body: SynthesizeRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    char_count = len(body.text)

    try:
        await check_quota(user["id"], "tts", char_count)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc

    background_tasks.add_task(record_usage, user["id"], "tts", char_count)

    cfg = get_settings()

    async def _stream_chunks():
        async with httpx.AsyncClient(timeout=60.0) as http:
            async with http.stream(
                "POST",
                f"https://api.elevenlabs.io/v1/text-to-speech/{body.voice_id}/stream",
                headers={"xi-api-key": cfg.elevenlabs_api_key, "Content-Type": "application/json"},
                json={
                    "text": body.text,
                    "model_id": body.model_id,
                    "voice_settings": {
                        "stability": body.stability,
                        "similarity_boost": body.similarity_boost,
                    },
                    "output_format": "mp3_44100_128",
                },
            ) as response:
                if response.status_code != 200:
                    logger.error("ElevenLabs stream error", status=response.status_code)
                    return
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    yield chunk

    return StreamingResponse(_stream_chunks(), media_type="audio/mpeg")


# ── Translation ──────────────────────────────────────────────────────────────

@router.post("/translate", response_model=TranslationResponse)
async def translate(
    body: TranslateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> TranslationResponse:
    char_count = len(body.text)

    try:
        await check_quota(user["id"], "translate", char_count)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc

    cfg = get_settings()
    translated = body.text
    source_lang = body.source_language

    # Try OpenAI first, fall back to Google Translate
    if cfg.openai_api_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=cfg.openai_api_key)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": (
                            f"Translate the following text to {body.target_language}. "
                            "Return only the translated text, no explanations."
                        )},
                        {"role": "user", "content": body.text},
                    ],
                    max_tokens=2000,
                ),
                timeout=10.0,
            )
            translated = response.choices[0].message.content or body.text
        except Exception as exc:
            logger.warning("OpenAI translation failed, falling back", error=str(exc))

    if translated == body.text and cfg.openai_api_key == "":
        # Google Translate fallback
        try:
            from deep_translator import GoogleTranslator
            translated = await asyncio.to_thread(
                GoogleTranslator(source="auto", target=body.target_language).translate,
                body.text,
            )
        except Exception as exc:
            logger.error("Google Translate fallback failed", error=str(exc))

    background_tasks.add_task(record_usage, user["id"], "translate", char_count)

    return TranslationResponse(translated_text=translated, source_language=source_lang)


# ── Voice Management ─────────────────────────────────────────────────────────

@router.get("/voices", response_model=list[VoiceItem])
async def list_voices(user: dict = Depends(get_current_user)) -> list[VoiceItem]:
    client = _elevenlabs()
    try:
        result = await asyncio.to_thread(client._client.voices.get_all)  # type: ignore[attr-defined]
        return [
            VoiceItem(voice_id=v.voice_id, name=v.name, category=v.category or "premade")
            for v in result.voices
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to list voices: {exc}") from exc


@router.delete("/voices/{voice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(voice_id: str, user: dict = Depends(get_current_user)) -> None:
    client = _elevenlabs()
    try:
        await asyncio.to_thread(client._client.voices.delete, voice_id)  # type: ignore[attr-defined]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to delete voice: {exc}") from exc


@router.post("/clone-voice", response_model=CloneVoiceResponse)
async def clone_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(default=""),
    user: dict = Depends(get_current_user),
) -> CloneVoiceResponse:
    try:
        await check_quota(user["id"], "clone", 1)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc

    audio_bytes = await audio.read()
    client = _elevenlabs()

    try:
        voice = await asyncio.to_thread(
            client._client.voices.ivc.create,  # type: ignore[attr-defined]
            name=name,
            description=description,
            files=[("audio", (audio.filename or "audio.wav", audio_bytes, "audio/wav"))],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Voice cloning failed: {exc}") from exc

    background_tasks.add_task(record_usage, user["id"], "clone", 1)

    return CloneVoiceResponse(voice_id=voice.voice_id, name=voice.name)
