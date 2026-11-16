"""
Twilio Media Streams bridge for translated phone calls.

Receives mulaw audio from two call legs (user + dest), runs STT → translate → TTS
per leg, and sends translated mulaw back to the other leg.
"""

from __future__ import annotations

import asyncio
import audioop
import json
import base64
import io
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog
from elevenlabs import AsyncElevenLabs

from app.config import get_settings
from app.services.text_filter import strip_non_verbal
from app.services.usage import QuotaExceededError, check_and_record_quota, refund_usage

# Shared premade ElevenLabs voice ("Rachel"). The old code used the account's first
# voice, which can be another user's cloned voice, and listed voices on every chunk.
DEFAULT_CALL_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
_MULAW_RATE = 8000  # Twilio Media Streams: 8 kHz mono mu-law, 1 byte per sample

logger = structlog.get_logger(__name__)

# Session metadata: session_id -> { user_id, target_lang }
_session_meta: dict[str, dict[str, Any]] = {}
# Active leg WebSockets: session_id -> { "user": (ws, stream_sid), "dest": (ws, stream_sid) }
_sessions: dict[str, dict[str, tuple[Any, str]]] = {}


def register_session(session_id: str, user_id: str, target_lang: str) -> None:
    """Register session metadata when creating a call."""
    _session_meta[session_id] = {"user_id": user_id, "target_lang": target_lang}
    _sessions[session_id] = {}


def get_other_leg(session_id: str, leg: str) -> tuple[Any, str] | None:
    """Return (websocket, stream_sid) for the other leg, or None."""
    legs = _sessions.get(session_id, {})
    other = "dest" if leg == "user" else "user"
    return legs.get(other)


def register_leg(session_id: str, leg: str, ws: Any, stream_sid: str) -> None:
    """Register a connected leg."""
    if session_id not in _sessions:
        _sessions[session_id] = {}
    _sessions[session_id][leg] = (ws, stream_sid)
    logger.info("Leg registered", session_id=session_id, leg=leg, stream_sid=stream_sid)


def unregister_leg(session_id: str, leg: str) -> None:
    """Remove a leg when disconnected."""
    if session_id in _sessions:
        _sessions[session_id].pop(leg, None)
        if not _sessions[session_id]:
            _sessions.pop(session_id, None)
    _session_meta.pop(session_id, None)


def get_session_meta(session_id: str) -> dict[str, Any] | None:
    return _session_meta.get(session_id)


@dataclass
class AudioProcessor:
    """Accumulates mulaw chunks and processes through STT → translate → TTS."""

    leg: str
    session_id: str
    target_lang: str
    user_id: str
    _buffer: bytearray = field(default_factory=bytearray)
    _pending: deque[asyncio.Task[None]] = field(default_factory=deque)
    _min_samples: int = 12000  # ~1.5s at 8kHz
    _client: Any = None
    _order: asyncio.Lock = field(default_factory=asyncio.Lock)
    _heard_ms: int = 0
    _charged_sec: int = 0
    _stopped: bool = False

    def _eleven(self) -> AsyncElevenLabs:
        """One ElevenLabs client per call leg instead of one per chunk."""
        if self._client is None:
            self._client = AsyncElevenLabs(api_key=get_settings().elevenlabs_api_key, timeout=30.0)
        return self._client

    def _stt_seconds_due(self, n_bytes: int) -> int:
        """Whole STT seconds to charge for this chunk, carrying the fraction to the next."""
        self._heard_ms += n_bytes * 1000 // _MULAW_RATE
        due = self._heard_ms // 1000 - self._charged_sec
        self._charged_sec += due
        return due

    async def add_chunk(self, mulaw_b64: str) -> None:
        """Add mulaw chunk and process if buffer is full."""
        try:
            data = base64.b64decode(mulaw_b64)
        except Exception as e:
            logger.warning("Invalid base64 media payload", error=str(e))
            return
        self._buffer.extend(data)
        if len(self._buffer) >= self._min_samples:
            chunk = bytes(self._buffer[: self._min_samples])
            self._buffer = bytearray(self._buffer[self._min_samples :])
            # Drop finished tasks so a long call does not keep every chunk's task alive.
            while self._pending and self._pending[0].done():
                self._pending.popleft()
            task = asyncio.create_task(self._process(chunk))
            self._pending.append(task)

    async def _charge(self, event_type: str, quantity: int) -> bool:
        """Reserve usage for this call's owner. False (and stop the leg) when over quota."""
        if quantity <= 0:
            return True
        try:
            await check_and_record_quota(self.user_id, event_type, quantity)
            return True
        except QuotaExceededError:
            logger.info("Translated call quota exhausted", leg=self.leg, event_type=event_type)
            self._stopped = True
            return False

    async def _process(self, mulaw_bytes: bytes) -> None:
        """Run STT → translate → TTS and send to other leg.

        Chunks run one at a time per leg so translated audio reaches the other
        caller in the order it was spoken. Every step is metered against the
        caller's plan like the desktop proxy; a failed paid call is refunded.
        """
        async with self._order:
            if self._stopped:
                return
            try:
                stt_sec = self._stt_seconds_due(len(mulaw_bytes))
                if not await self._charge("stt", stt_sec):
                    return
                try:
                    text = await self._stt(mulaw_bytes)
                except Exception:
                    await refund_usage(self.user_id, "stt", stt_sec)
                    raise
                text = strip_non_verbal(text or "")
                if not text:
                    return
                if not await self._charge("translate", len(text)):
                    return
                translated = strip_non_verbal(await self._translate(text))
                if not translated:
                    return
                if not await self._charge("tts", len(translated)):
                    return
                try:
                    mulaw_out = await self._tts(translated)
                except Exception:
                    await refund_usage(self.user_id, "tts", len(translated))
                    raise
                if mulaw_out:
                    await self._send_to_other_leg(mulaw_out)
            except Exception as e:
                logger.exception("Audio processing failed", leg=self.leg, error=str(e))

    async def _stt(self, mulaw_bytes: bytes) -> str:
        """Convert mulaw to WAV and transcribe via ElevenLabs."""
        cfg = get_settings()
        if not cfg.elevenlabs_api_key:
            return ""

        # Mulaw 8kHz -> PCM 16-bit 16kHz for ElevenLabs (scribe prefers 16kHz)
        pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
        pcm_16k = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)[0]

        wav_buf = io.BytesIO()
        wav_buf.write(b"RIFF")
        wav_buf.write((36 + len(pcm_16k)).to_bytes(4, "little"))
        wav_buf.write(b"WAVEfmt ")
        wav_buf.write((16).to_bytes(4, "little"))
        wav_buf.write((1).to_bytes(2, "little"))  # PCM
        wav_buf.write((1).to_bytes(2, "little"))  # mono
        wav_buf.write((16000).to_bytes(4, "little"))
        wav_buf.write((32000).to_bytes(4, "little"))
        wav_buf.write((2).to_bytes(2, "little"))
        wav_buf.write((16).to_bytes(2, "little"))
        wav_buf.write(b"data")
        wav_buf.write(len(pcm_16k).to_bytes(4, "little"))
        wav_buf.write(pcm_16k)

        # AsyncElevenLabs has no ``_client``; the old call raised on every chunk.
        result = await self._eleven().speech_to_text.convert(
            file=("audio.wav", wav_buf.getvalue(), "audio/wav"),
            model_id="scribe_v1",
        )
        return getattr(result, "text", "") or ""

    async def _translate(self, text: str) -> str:
        """Translate text to target language."""
        cfg = get_settings()
        if cfg.openai_api_key:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=cfg.openai_api_key)
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {
                                "role": "system",
                                "content": f"Translate to {self.target_lang}. Return only the translated text.",
                            },
                            {"role": "user", "content": text},
                        ],
                        max_tokens=500,
                    ),
                    timeout=10.0,
                )
                return (response.choices[0].message.content or text).strip()
            except Exception as e:
                logger.warning("OpenAI translate failed", error=str(e))

        try:
            from deep_translator import GoogleTranslator

            return await asyncio.to_thread(
                GoogleTranslator(source="auto", target=self.target_lang).translate,
                text,
            )
        except Exception as e:
            logger.warning("Google translate failed", error=str(e))
            return text

    async def _tts(self, text: str) -> bytes | None:
        """Synthesize text to mulaw 8kHz via ElevenLabs."""
        cfg = get_settings()
        if not cfg.elevenlabs_api_key:
            return None

        # Ask for 8 kHz mu-law directly: it is what Twilio plays, so there is no MP3
        # decode (ffmpeg via pydub) on the event loop for every chunk.
        stream = self._eleven().text_to_speech.convert(
            DEFAULT_CALL_VOICE_ID,
            text=text,
            model_id="eleven_flash_v2_5",
            output_format="ulaw_8000",
        )
        return b"".join([chunk async for chunk in stream])

    async def _send_to_other_leg(self, mulaw_bytes: bytes) -> None:
        """Send mulaw to the other leg's WebSocket."""
        other = get_other_leg(self.session_id, self.leg)
        if not other:
            return
        ws, stream_sid = other
        payload_b64 = base64.b64encode(mulaw_bytes).decode("ascii")
        msg = json.dumps(
            {"event": "media", "streamSid": stream_sid, "media": {"payload": payload_b64}}
        )
        try:
            await ws.send_text(msg)
        except Exception as e:
            logger.warning("Failed to send media to other leg", error=str(e))

    async def drain(self) -> None:
        """Wait for pending processing tasks."""
        while self._pending:
            t = self._pending.popleft()
            try:
                await t
            except Exception as e:
                logger.warning("Pending task failed", error=str(e))
