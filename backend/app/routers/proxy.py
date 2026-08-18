"""
Proxy endpoints — forward requests to ElevenLabs/OpenAI after quota checks.

API keys live only here on the server and are never sent to the desktop app.
"""

from __future__ import annotations

import asyncio
import io

import httpx
import structlog
from elevenlabs import AsyncElevenLabs, VoiceSettings
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.requests import SynthesizeRequest, TranslateRequest
from app.models.responses import (
    CloneVoiceResponse,
    TranscriptionResponse,
    TranslationResponse,
    VoiceItem,
)
from app.services.supabase_client import get_supabase
from app.services.text_filter import strip_non_verbal
from app.services.usage import QuotaExceededError, check_and_record_quota, check_quota

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])

UPGRADE_URL = "https://www.livetranslate.net/upgrade"


def _audio_duration_seconds(audio_bytes: bytes, content_type: str | None, fallback_rate: int) -> float:
    """Get audio duration in seconds; use parsed duration when possible, else format-appropriate heuristic."""
    fmt: str | None = None
    if content_type:
        ctl = content_type.lower()
        if "wav" in ctl or "wave" in ctl:
            fmt = "wav"
        elif "mp3" in ctl or "mpeg" in ctl:
            fmt = "mp3"
        elif "ogg" in ctl:
            fmt = "ogg"
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
        return max(1.0, len(seg) / 1000.0)
    except Exception as exc:
        logger.warning(
            "audio_duration_parse_failed",
            content_type=content_type,
            fmt=fmt,
            error=str(exc),
            exc_info=True,
        )
    # Compressed format: estimate from size and bitrate (bits per second)
    if fmt in ("mp3", "ogg") or (content_type and any(
        x in (content_type or "").lower() for x in ("mp3", "mpeg", "ogg")
    )):
        bitrate_bps = 128_000  # default 128 kbps if not readable from header
        if fmt == "mp3" and len(audio_bytes) >= 128:
            try:
                idx = 0
                if audio_bytes[:3] == b"ID3":
                    size = (audio_bytes[6] << 21 | audio_bytes[7] << 14 | audio_bytes[8] << 7 | audio_bytes[9]) & 0x7FFFFFFF
                    idx = 10 + size
                if idx + 4 <= len(audio_bytes):
                    b0, b1 = audio_bytes[idx], audio_bytes[idx + 1]
                    # MPEG layer3 bitrate index (simplified)
                    br_table = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
                    if (b0 & 0xFF) == 0xFF and (b1 & 0xE0) == 0xE0:
                        br_idx = (b1 >> 4) & 0x0F
                        if br_idx < len(br_table):
                            bitrate_bps = br_table[br_idx] * 1000
            except Exception:
                pass
        duration_sec = (len(audio_bytes) * 8) / bitrate_bps
        return max(1.0, duration_sec)
    # Uncompressed / wav: PCM heuristic (16-bit mono bytes per second = sample_rate * 2)
    return max(1.0, len(audio_bytes) / (fallback_rate * 2))


def _elevenlabs() -> AsyncElevenLabs:
    """Return an async ElevenLabs client using configured API key."""
    cfg = get_settings()
    key = (cfg.elevenlabs_api_key or "").strip()
    if not key or key.startswith("your-") or not key.startswith("sk_"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Server ElevenLabs API key is missing or invalid. "
                "Set Railway ELEVENLABS_API_KEY to a secret key that starts with sk_ "
                "(not an API key ID)."
            ),
        )
    return AsyncElevenLabs(api_key=key, timeout=60.0)


def _elevenlabs_upstream_error(action: str, exc: Exception) -> HTTPException:
    """Map ElevenLabs failures to a short client-facing detail (avoid dumping headers)."""
    text = str(exc)
    lower = text.lower()
    if "api_key_id_used_as_api_key" in lower or (
        "invalid_api_key" in lower and "sk_" in lower
    ):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{action} failed: server ELEVENLABS_API_KEY is an API key ID, not a secret. "
                "Replace it on Railway with a key that starts with sk_."
            ),
        )
    if "quota_exceeded" in lower or "0 credits remaining" in lower:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{action} failed: ElevenLabs account has no credits left. "
                "Top up the LiveTranslate ElevenLabs workspace or use a key with remaining quota."
            ),
        )
    if "voice_not_found" in lower or "voice does not exist" in lower:
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"{action} failed: voice not found on the server ElevenLabs account. "
                "Re-clone the voice after changing API keys."
            ),
        )
    if "authentication_error" in lower or "unauthorized" in lower:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{action} failed: server ElevenLabs API key rejected. Check Railway ELEVENLABS_API_KEY.",
        )
    # Prefer ElevenLabs' own message when present in the SDK exception text.
    message = ""
    for marker in ("'message': '", '"message": "'):
        start = text.find(marker)
        if start >= 0:
            start += len(marker)
            end = text.find("'", start) if marker.endswith("'") else text.find('"', start)
            if end > start:
                message = text[start:end]
                break
    if not message:
        for marker in ("'code': '", '"code": "'):
            start = text.find(marker)
            if start >= 0:
                start += len(marker)
                end = text.find("'", start) if marker.endswith("'") else text.find('"', start)
                if end > start:
                    message = text[start:end]
                    break
    logger.error("ElevenLabs upstream error", action=action, error=text[:500])
    if message:
        return HTTPException(status_code=502, detail=f"{action} failed: {message[:180]}")
    return HTTPException(status_code=502, detail=f"{action} failed: upstream error")


def _quota_error(exc: QuotaExceededError) -> HTTPException:
    """Build HTTP 402 response for quota exceeded."""
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
    audio: UploadFile = File(...),  # noqa: B008
    language: str = Form(default="auto"),  # noqa: B008
    sample_rate: str = Form(default="16000"),  # noqa: B008
    user: dict = Depends(get_current_user),  # noqa: B008
) -> TranscriptionResponse:
    """Transcribe audio to text via ElevenLabs STT; record STT usage by duration."""
    audio_bytes = await audio.read()
    rate = int(sample_rate) if sample_rate.isdigit() else 16000
    rate = max(8000, min(48000, rate))

    duration_seconds = _audio_duration_seconds(audio_bytes, audio.content_type, rate)

    try:
        await check_and_record_quota(user["id"], "stt", int(round(duration_seconds)))
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("STT quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc

    client = _elevenlabs()
    try:
        # Named tuple so ElevenLabs multipart includes a filename (bare BytesIO can 500 upstream).
        audio_file = ("audio.wav", audio_bytes, "audio/wav")
        result = await client.speech_to_text.convert(
            file=audio_file,
            model_id="scribe_v1",
            language_code=None if language == "auto" else language,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _elevenlabs_upstream_error("Transcription", exc) from exc

    lang = getattr(result, "language_code", None) or language or "auto"
    return TranscriptionResponse(
        text=(getattr(result, "text", None) or "").strip(),
        language_code=str(lang),
    )


# ── TTS ─────────────────────────────────────────────────────────────────────

@router.post("/synthesize")
async def synthesize(
    body: SynthesizeRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """Synthesize text to MP3 via ElevenLabs TTS; record usage by character count."""
    text = strip_non_verbal(body.text or "")
    if not text:
        raise HTTPException(status_code=400, detail="Text must not be empty")
    char_count = len(text)
    # ~14 chars/sec spoken English — aligns sold dubbing minutes with TTS output.
    estimated_dub_sec = max(1, int(round(char_count / 14.0)))

    try:
        await check_and_record_quota(user["id"], "tts", char_count)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("TTS quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc

    try:
        await check_and_record_quota(user["id"], "dub", estimated_dub_sec)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dubbing quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc

    client = _elevenlabs()
    try:
        audio_stream = client.text_to_speech.convert(
            body.voice_id,
            text=text,
            model_id=body.model_id,
            output_format="mp3_44100_128",
            voice_settings=VoiceSettings(
                stability=body.stability, similarity_boost=body.similarity_boost
            ),
        )
        audio_data = b"".join([chunk async for chunk in audio_stream])
    except HTTPException:
        raise
    except Exception as exc:
        raise _elevenlabs_upstream_error("Synthesis", exc) from exc

    # Reconcile dubbing seconds if actual audio is longer than the estimate.
    try:
        duration_sec = _audio_duration_seconds(audio_data, "audio/mpeg", 44100)
        actual = max(1, int(round(duration_sec)))
        extra = actual - estimated_dub_sec
        if extra > 0:
            await check_and_record_quota(user["id"], "dub", extra)
    except QuotaExceededError:
        logger.info(
            "Dubbing overage after estimate not recorded (quota hit)",
            user_id=user["id"],
            estimated=estimated_dub_sec,
        )
    except Exception as exc:
        logger.warning("Failed to reconcile dubbing usage", error=str(exc))

    return StreamingResponse(io.BytesIO(audio_data), media_type="audio/mpeg")


async def _stream_tts_chunks(voice_id: str, text: str, model_id: str, stability: float, similarity_boost: float):
    """Yield TTS audio chunks from ElevenLabs stream API."""
    cfg = get_settings()
    async with httpx.AsyncClient(timeout=60.0) as http:
        async with http.stream(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            headers={"xi-api-key": cfg.elevenlabs_api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": model_id,
                "voice_settings": {"stability": stability, "similarity_boost": similarity_boost},
                "output_format": "mp3_44100_128",
            },
        ) as response:
            if response.status_code != 200:
                err_body = await response.aread()
                logger.error(
                    "ElevenLabs stream error",
                    status=response.status_code,
                    body=err_body[:500],
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=err_body.decode("utf-8", errors="replace") if err_body else "Stream failed",
                )
            async for chunk in response.aiter_bytes(chunk_size=4096):
                yield chunk


@router.post("/synthesize/stream")
async def synthesize_stream(
    body: SynthesizeRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """Stream TTS audio chunks from ElevenLabs."""
    text = strip_non_verbal(body.text or "")
    if not text:
        raise HTTPException(status_code=400, detail="Text must not be empty")
    estimated_dub_sec = max(1, int(round(len(text) / 14.0)))
    try:
        await check_and_record_quota(user["id"], "tts", len(text))
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("TTS stream quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc
    try:
        await check_and_record_quota(user["id"], "dub", estimated_dub_sec)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dubbing stream quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc
    return StreamingResponse(
        _stream_tts_chunks(
            body.voice_id,
            text,
            body.model_id,
            body.stability,
            body.similarity_boost,
        ),
        media_type="audio/mpeg",
    )


# ── Translation ──────────────────────────────────────────────────────────────

@router.post("/translate", response_model=TranslationResponse)
async def translate(
    body: TranslateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> TranslationResponse:
    """Translate text via OpenAI or Google fallback; record translation usage by char count."""
    char_count = len(body.text)

    try:
        await check_and_record_quota(user["id"], "translate", char_count)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Translate quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc

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

    if translated == body.text:
        # Google Translate fallback (e.g. when OpenAI not configured or failed)
        try:
            from deep_translator import GoogleTranslator

            target = body.target_language.strip().lower()
            # Align app language codes with Google Translate ids.
            target = {"zh": "zh-CN", "fil": "tl", "iw": "he"}.get(target, target)
            translated = await asyncio.to_thread(
                GoogleTranslator(source="auto", target=target).translate,
                body.text,
            )
        except Exception as exc:
            logger.error("Google Translate fallback failed", error=str(exc))

    return TranslationResponse(
        translated_text=(translated or "").strip() or body.text,
        source_language=source_lang or "auto",
    )


# ── Voice Management ─────────────────────────────────────────────────────────

@router.get("/voices", response_model=list[VoiceItem])
async def list_voices(user: dict = Depends(get_current_user)) -> list[VoiceItem]:  # noqa: B008
    """Return all ElevenLabs voices available to the user."""
    client = _elevenlabs()
    try:
        result = await client.voices.get_all()
        items: list[VoiceItem] = []
        for v in result.voices:
            vid = str(getattr(v, "voice_id", "") or "").strip()
            if not vid:
                continue
            raw_cat = getattr(v, "category", None)
            if raw_cat is None:
                cat = "premade"
            elif hasattr(raw_cat, "value"):
                cat = str(raw_cat.value)
            else:
                cat = str(raw_cat) or "premade"
            name = str(getattr(v, "name", None) or "").strip() or vid
            items.append(VoiceItem(voice_id=vid, name=name, category=cat))
        return items
    except HTTPException:
        raise
    except Exception as exc:
        raise _elevenlabs_upstream_error("List voices", exc) from exc


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str, user: dict = Depends(get_current_user)) -> Response:  # noqa: B008
    """Delete a cloned voice from ElevenLabs and ownership record if owned by user."""
    client = _elevenlabs()
    try:
        all_voices = await client.voices.get_all()
        voice_meta = next((v for v in all_voices.voices if v.voice_id == voice_id), None)
        if voice_meta is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice not found")
        if getattr(voice_meta, "category", None) == "premade":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete premium or system voices")
        sb = await get_supabase()
        delete_result = (
            await sb.table("user_voices")
            .delete()
            .eq("voice_id", voice_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if not delete_result.data or len(delete_result.data) == 0:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to delete this voice")
        await client.voices.delete(voice_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _elevenlabs_upstream_error("Delete voice", exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/voices/{voice_id}", response_model=CloneVoiceResponse)
async def rename_voice(
    voice_id: str,
    user: dict = Depends(get_current_user),  # noqa: B008
    name: str = Form(...),  # noqa: B008
) -> CloneVoiceResponse:
    """Rename a user-owned cloned voice on ElevenLabs."""
    new_name = (name or "").strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name must not be empty")

    client = _elevenlabs()
    try:
        all_voices = await client.voices.get_all()
        voice_meta = next((v for v in all_voices.voices if v.voice_id == voice_id), None)
        if voice_meta is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice not found")
        if getattr(voice_meta, "category", None) == "premade":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot rename premade voices")

        sb = await get_supabase()
        owned = (
            await sb.table("user_voices")
            .select("voice_id")
            .eq("voice_id", voice_id)
            .eq("user_id", user["id"])
            .maybe_single()
            .execute()
        )
        if not owned.data:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to rename this voice")

        key = get_settings().elevenlabs_api_key
        if not key:
            raise HTTPException(status_code=503, detail="ElevenLabs API key not configured")
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.post(
                f"https://api.elevenlabs.io/v1/voices/{voice_id}/edit",
                headers={"xi-api-key": key},
                data={"name": new_name},
            )
        if resp.status_code >= 400:
            raise _elevenlabs_upstream_error(
                "Rename voice",
                Exception(f"status_code: {resp.status_code}, body: {resp.text[:800]}"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise _elevenlabs_upstream_error("Rename voice", exc) from exc
    return CloneVoiceResponse(voice_id=voice_id, name=new_name)


@router.post("/clone-voice", response_model=CloneVoiceResponse)
async def clone_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),  # noqa: B008
    name: str = Form(...),  # noqa: B008
    description: str = Form(default=""),  # noqa: B008
    user: dict = Depends(get_current_user),  # noqa: B008
) -> CloneVoiceResponse:
    """Create a new cloned voice from uploaded audio and record ownership."""
    # Check first; only record usage after ElevenLabs + ownership succeed so
    # failed clones (or API key migrations) do not burn the free-tier slot.
    try:
        await check_quota(user["id"], "clone", 1)
    except QuotaExceededError as exc:
        raise _quota_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Clone quota check failed", user_id=user["id"])
        raise HTTPException(status_code=503, detail=f"Usage metering unavailable: {exc}") from exc

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")

    cfg = get_settings()
    key = (cfg.elevenlabs_api_key or "").strip()
    if not key or key.startswith("your-") or not key.startswith("sk_"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Server ElevenLabs API key is missing or invalid. "
                "Set Railway ELEVENLABS_API_KEY to a secret key that starts with sk_."
            ),
        )

    # Use raw multipart — the SDK has sent labels incorrectly on some builds
    # ("Labels must be serialized dictionary object").
    form_data: dict[str, str] = {
        "name": name.strip() or "cloned_voice",
        "labels": "{}",
    }
    desc = (description or "").strip()
    if desc:
        form_data["description"] = desc

    try:
        async with httpx.AsyncClient(timeout=120.0) as http:
            resp = await http.post(
                "https://api.elevenlabs.io/v1/voices/add",
                headers={"xi-api-key": key},
                data=form_data,
                files={
                    "files": (audio.filename or "audio.wav", audio_bytes, "audio/wav"),
                },
            )
        if resp.status_code >= 400:
            raise _elevenlabs_upstream_error(
                "Voice cloning",
                Exception(f"status_code: {resp.status_code}, body: {resp.text[:800]}"),
            )
        payload = resp.json()
        voice_id = str(payload.get("voice_id") or "").strip()
        voice_name = str(payload.get("name") or name).strip() or name
        if not voice_id:
            raise HTTPException(status_code=502, detail="Voice cloning failed: no voice_id returned")
    except HTTPException:
        raise
    except Exception as exc:
        raise _elevenlabs_upstream_error("Voice cloning", exc) from exc

    sb = await get_supabase()
    try:
        await sb.table("user_voices").insert({
            "voice_id": voice_id,
            "user_id": user["id"],
        }).execute()
    except Exception as exc:
        try:
            client = _elevenlabs()
            await client.voices.delete(voice_id)
        except Exception as cleanup_exc:
            logger.warning(
                "Orphaned ElevenLabs voice after Supabase insert failure; cleanup delete failed",
                voice_id=voice_id,
                user_id=user["id"],
                cleanup_error=str(cleanup_exc),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Voice created but failed to record ownership; please try again or contact support.",
        ) from exc

    try:
        await check_and_record_quota(user["id"], "clone", 1)
    except QuotaExceededError as exc:
        # Race: another clone consumed the slot after our pre-check.
        try:
            await sb.table("user_voices").delete().eq("voice_id", voice_id).eq(
                "user_id", user["id"]
            ).execute()
            client = _elevenlabs()
            await client.voices.delete(voice_id)
        except Exception as cleanup_exc:
            logger.warning(
                "Clone quota race cleanup failed",
                voice_id=voice_id,
                user_id=user["id"],
                cleanup_error=str(cleanup_exc),
            )
        raise _quota_error(exc) from exc

    return CloneVoiceResponse(voice_id=voice_id, name=voice_name)
