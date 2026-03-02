"""
ElevenLabs API service for STT, TTS, voice cloning, and dubbing.
"""

import asyncio
import contextlib
import io
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# OpenAI client for translation
_openai_client = None


def _get_openai_client() -> object | None:
    """Get or create OpenAI client for translation."""
    global _openai_client
    if _openai_client is None:
        try:
            from openai import AsyncOpenAI
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                logger.debug("OPENAI_API_KEY not set, skipping OpenAI translation")
                return None
            _openai_client = AsyncOpenAI(api_key=api_key)
            logger.info("OpenAI client initialized for translation")
        except ImportError:
            logger.warning("OpenAI package not installed, translation will be disabled")
        except Exception as e:
            logger.warning("Failed to create OpenAI client", error=str(e))
    return _openai_client


@dataclass
class Voice:
    """ElevenLabs voice information."""

    voice_id: str
    name: str
    category: str = "cloned"
    description: str | None = None


@dataclass
class TranscriptionResult:
    """Result from speech-to-text."""

    text: str
    language_code: str
    confidence: float
    is_final: bool = True


@dataclass
class DubbingResult:
    """Result from dubbing API."""

    audio: bytes
    translated_text: str = ""
    source_text: str = ""


class ElevenLabsService:
    """
    Service wrapper for ElevenLabs API.

    Provides:
    - Speech-to-Text via Scribe v2
    - Text-to-Speech via Flash v2.5
    - Instant Voice Cloning
    - Speech-to-Speech translation
    """

    # Supported languages
    SUPPORTED_LANGUAGES = {
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "zh": "Chinese",
        "id": "Indonesian",
        "th": "Thai",
        "ru": "Russian",
        "hi": "Hindi",
        "vi": "Vietnamese",
        "tl": "Filipino",
    }

    def __init__(self, api_key: str, openai_api_key: str | None = None) -> None:
        """
        Initialize ElevenLabs service.

        Args:
            api_key: ElevenLabs API key
            openai_api_key: Optional OpenAI API key for translation
        """
        self._api_key = api_key
        self._openai_api_key = openai_api_key
        self._client: Any = None
        self._async_client: Any = None

    def _get_client(self) -> Any:
        """Get or create sync ElevenLabs client."""
        if self._client is None:
            from elevenlabs.client import ElevenLabs

            self._client = ElevenLabs(
                api_key=self._api_key,
                timeout=30.0,  # HTTP-level timeout in seconds
            )
        return self._client

    def _get_async_client(self) -> Any:
        """Get or create async ElevenLabs client."""
        if self._async_client is None:
            from elevenlabs.client import AsyncElevenLabs

            self._async_client = AsyncElevenLabs(
                api_key=self._api_key,
                timeout=30.0,  # HTTP-level timeout in seconds
            )
        return self._async_client

    async def clone_voice(
        self,
        audio_data: bytes,
        name: str,
        description: str | None = None,
        _retry_after_cleanup: bool = True,
    ) -> str:
        """
        Clone a voice from audio data using Instant Voice Cloning.

        Args:
            audio_data: Audio bytes (WAV or MP3)
            name: Name for the cloned voice
            description: Optional description
            _retry_after_cleanup: Internal flag to prevent infinite recursion

        Returns:
            Voice ID of the cloned voice
        """
        try:
            client = self._get_client()

            # Create voice clone via IVC - run in thread pool to avoid blocking async loop
            def _clone() -> Any:
                return client.voices.ivc.create(
                    name=name,
                    description=description or f"Cloned voice: {name}",
                    files=[("audio.wav", audio_data)],
                )

            voice = await asyncio.to_thread(_clone)

            logger.info("Voice cloned successfully", voice_id=voice.voice_id, name=name)
            return str(voice.voice_id)

        except Exception as e:
            error_str = str(e)
            # Handle voice limit reached by deleting old cloned voices
            if "voice_limit_reached" in error_str and _retry_after_cleanup:
                logger.warning("Voice limit reached, cleaning up old cloned voices...")
                deleted = await self._cleanup_old_cloned_voices(keep_count=25)
                if deleted > 0:
                    logger.info("Deleted old voices, retrying clone", deleted_count=deleted)
                    return await self.clone_voice(
                        audio_data, name, description, _retry_after_cleanup=False
                    )
            logger.exception("Failed to clone voice", error=error_str)
            raise

    async def _cleanup_old_cloned_voices(self, keep_count: int = 25) -> int:
        """
        Delete old cloned voices to make room for new ones.

        Args:
            keep_count: Number of cloned voices to keep (delete oldest beyond this)

        Returns:
            Number of voices deleted
        """
        try:
            voices = await self.list_voices()
            # Filter to only cloned voices (category == "cloned")
            cloned = [v for v in voices if v.category == "cloned"]

            if len(cloned) <= keep_count:
                logger.info("Not enough cloned voices to cleanup", count=len(cloned))
                return 0

            # Sort by name (which often contains timestamp) and delete oldest
            # Delete voices beyond keep_count
            to_delete = cloned[keep_count:]
            deleted = 0

            for voice in to_delete:
                if await self.delete_voice(voice.voice_id):
                    deleted += 1

            logger.info("Cleaned up old cloned voices", deleted=deleted, kept=keep_count)
            return deleted

        except Exception as e:
            logger.exception("Failed to cleanup old voices", error=str(e))
            return 0

    async def clone_voice_from_file(
        self,
        file_path: str,
        name: str,
        description: str | None = None,
    ) -> str:
        """
        Clone a voice from an audio file.

        Args:
            file_path: Path to audio file
            name: Name for the cloned voice
            description: Optional description

        Returns:
            Voice ID of the cloned voice
        """
        with open(file_path, "rb") as f:
            audio_data = f.read()
        return await self.clone_voice(audio_data, name, description)

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
    ) -> TranscriptionResult:
        """
        Transcribe audio to text using Scribe v2.

        Args:
            audio_data: Audio bytes
            language: Language code or "auto" for detection

        Returns:
            TranscriptionResult with transcribed text
        """
        try:
            # Use async client for proper timeout handling
            client = self._get_async_client()

            # Use speech-to-text endpoint with timeout
            try:
                result = await asyncio.wait_for(
                    client.speech_to_text.convert(
                        file=("audio.wav", audio_data),
                        model_id="scribe_v2",
                        language_code=language if language and language != "auto" else None,
                    ),
                    timeout=15.0  # 15 second timeout - asyncio can cancel async calls
                )
            except asyncio.TimeoutError as e:
                logger.error("Transcription timed out after 15s")
                raise RuntimeError("Transcription timed out") from e

            logger.info("Transcription completed", text_len=len(result.text) if result.text else 0)

            return TranscriptionResult(
                text=result.text,
                language_code=result.language_code if hasattr(result, "language_code") else "en",
                confidence=result.confidence if hasattr(result, "confidence") else 0.9,
                is_final=True,
            )

        except Exception as e:
            logger.exception("Transcription failed", error=str(e))
            raise

    # Map our language codes to Google Translate codes
    _GOOGLE_LANG_MAP: dict[str, str] = {
        "en": "en",
        "ja": "ja",
        "ko": "ko",
        "zh": "zh-CN",
        "id": "id",
        "th": "th",
        "ru": "ru",
        "hi": "hi",
        "vi": "vi",
        "tl": "tl",
    }

    async def translate_text(self, text: str, target_language: str) -> str:
        """
        Translate text to target language.

        Tries OpenAI GPT first (if key available), falls back to
        Google Translate via deep-translator (free, no key needed).

        Args:
            text: Text to translate
            target_language: Target language code (e.g., "en", "ja", "ko")

        Returns:
            Translated text
        """
        if not text.strip():
            return text

        # Try OpenAI first if available
        translated = await self._translate_with_openai(text, target_language)
        if translated is not None:
            return translated

        # Fall back to Google Translate (free, no API key needed)
        translated = await self._translate_with_google(text, target_language)
        if translated is not None:
            return translated

        logger.warning("All translation methods failed, returning original text")
        return text

    async def _translate_with_openai(self, text: str, target_language: str) -> str | None:
        """Translate using OpenAI GPT. Returns None if unavailable."""
        try:
            # Get language name from code
            target_lang_name = self.SUPPORTED_LANGUAGES.get(
                target_language, target_language
            )

            # Prefer instance key, then global client (env var)
            client: Any = None
            if self._openai_api_key:
                try:
                    from openai import AsyncOpenAI
                    client = AsyncOpenAI(api_key=self._openai_api_key)
                except (ImportError, Exception):
                    pass
            if client is None:
                client = _get_openai_client()
            if client is None:
                return None  # No OpenAI available, caller should try fallback

            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": f"You are a translator. Translate the following text to {target_lang_name}. "
                                       f"Only output the translated text, nothing else. "
                                       f"Preserve the tone and style of the original. "
                                       f"Remove any non-verbal markers like [MUSIC], (laughter), [PAUSE], ♪, etc. "
                                       f"Only output spoken words."
                        },
                        {
                            "role": "user",
                            "content": text
                        }
                    ],
                    temperature=0.3,
                    max_tokens=500,
                ),
                timeout=10.0,
            )

            content = response.choices[0].message.content
            translated = content.strip() if content else text
            with contextlib.suppress(Exception):
                logger.info(
                    "Translation completed (OpenAI)",
                    original_len=len(text),
                    translated_len=len(translated),
                    target_lang=target_language,
                )
            return str(translated)

        except asyncio.TimeoutError:
            logger.error("OpenAI translation timed out")
            return None
        except Exception as e:
            with contextlib.suppress(Exception):
                logger.debug("OpenAI translation unavailable", error=str(e))
            return None

    async def _translate_with_google(self, text: str, target_language: str) -> str | None:
        """Translate using Google Translate via deep-translator (free)."""
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            logger.warning("deep-translator not installed. Run: pip install deep-translator")
            return None

        google_lang = self._GOOGLE_LANG_MAP.get(target_language, target_language)

        try:
            def _do_translate() -> str:
                translator = GoogleTranslator(source="auto", target=google_lang)
                return translator.translate(text)

            translated = await asyncio.wait_for(
                asyncio.to_thread(_do_translate),
                timeout=10.0,
            )

            with contextlib.suppress(Exception):
                logger.info(
                    "Translation completed (Google)",
                    original_len=len(text),
                    translated_len=len(translated) if translated else 0,
                    target_lang=target_language,
                )
            return str(translated) if translated else text

        except asyncio.TimeoutError:
            logger.error("Google translation timed out")
            return None
        except Exception as e:
            with contextlib.suppress(Exception):
                logger.warning("Google translation failed", error=str(e))
            return None

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptionResult]:
        """
        Stream transcription for real-time audio.

        Args:
            audio_stream: Async iterator of audio chunks

        Yields:
            TranscriptionResult for each processed segment
        """
        # Buffer for accumulating audio
        audio_buffer = b""
        min_buffer_size = 16000 * 2  # ~1 second at 16kHz mono float32

        async for chunk in audio_stream:
            audio_buffer += chunk

            if len(audio_buffer) >= min_buffer_size:
                result = await self.transcribe(audio_buffer)
                audio_buffer = b""
                yield result

        # Process remaining audio
        if audio_buffer:
            result = await self.transcribe(audio_buffer)
            yield result

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
    ) -> bytes:
        """
        Synthesize speech from text.

        Args:
            text: Text to synthesize
            voice_id: Voice ID to use
            model_id: TTS model ID
            stability: Voice stability (0-1)
            similarity_boost: Voice similarity (0-1)

        Returns:
            Audio bytes (MP3)
        """
        try:
            client = self._get_client()

            from elevenlabs.types import VoiceSettings

            vs = VoiceSettings(stability=stability, similarity_boost=similarity_boost)

            # TTS synthesis - run in thread pool to avoid blocking async loop
            def _synthesize() -> Any:
                return client.text_to_speech.convert(
                    text=text,
                    voice_id=voice_id,
                    model_id=model_id,
                    voice_settings=vs,
                    request_options={"timeout_in_seconds": 30},
                )

            audio = await asyncio.to_thread(_synthesize)

            # SDK returns Iterator[bytes]; handle bytes or iterator
            if isinstance(audio, bytes):
                return audio
            audio_bytes = b"".join(audio)
            return audio_bytes

        except Exception as e:
            logger.exception("TTS synthesis failed", error=str(e))
            raise

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
    ) -> AsyncIterator[bytes]:
        """
        Stream synthesized speech.

        Args:
            text: Text to synthesize
            voice_id: Voice ID to use
            model_id: TTS model ID

        Yields:
            Audio chunks as bytes
        """
        try:
            client = self._get_client()

            audio_stream = client.text_to_speech.stream(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
            )

            for chunk in audio_stream:
                yield chunk

        except Exception as e:
            logger.exception("TTS streaming failed", error=str(e))
            raise

    async def dub_audio(
        self,
        audio_data: bytes,
        source_language: str,
        target_language: str,
        poll_interval: float = 1.0,
        max_wait_seconds: float = 120.0,
    ) -> DubbingResult | None:
        """
        Dub audio using ElevenLabs Dubbing API.

        This handles transcription, translation, and TTS in one API call.
        The dubbing API is batch-based, so this method polls for completion.

        Args:
            audio_data: Source audio bytes (WAV format preferred)
            source_language: Source language code (e.g., "en", "auto")
            target_language: Target language code (e.g., "vi", "ja")
            poll_interval: Seconds between status checks
            max_wait_seconds: Maximum time to wait for completion

        Returns:
            DubbingResult with audio and translated text, or None if failed
        """
        client = self._get_client()

        source_lang = source_language if source_language != "auto" else "auto"

        try:
            # Create a file-like object from audio bytes
            audio_file = io.BytesIO(audio_data)
            audio_file.name = "audio.wav"

            # Create dubbing job
            logger.info(
                "Creating dubbing job",
                source_lang=source_lang,
                target_lang=target_language,
                audio_size=len(audio_data),
            )

            dub_response = await asyncio.to_thread(
                client.dubbing.create,
                file=audio_file,
                source_lang=source_lang,
                target_lang=target_language,
                watermark=False,
                drop_background_audio=True,  # Keep only speech
            )

            dubbing_id = dub_response.dubbing_id
            expected_duration = getattr(dub_response, 'expected_duration_sec', None)

            logger.info(
                "Dubbing job created",
                dubbing_id=dubbing_id,
                expected_duration=expected_duration,
            )

            # Poll for completion
            start_time = time.time()
            while True:
                elapsed = time.time() - start_time
                if elapsed > max_wait_seconds:
                    logger.error("Dubbing timed out", dubbing_id=dubbing_id, elapsed=elapsed)
                    # Clean up
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(client.dubbing.delete, dubbing_id)
                    return None

                # Check status
                status_response = await asyncio.to_thread(
                    client.dubbing.get,
                    dubbing_id=dubbing_id
                )

                status = getattr(status_response, 'status', 'unknown')
                error = getattr(status_response, 'error', None)

                if error:
                    logger.error("Dubbing failed", dubbing_id=dubbing_id, error=error)
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(client.dubbing.delete, dubbing_id)
                    return None

                if status == 'dubbed':
                    logger.info("Dubbing completed", dubbing_id=dubbing_id, elapsed=elapsed)
                    break

                logger.debug("Dubbing in progress", dubbing_id=dubbing_id, status=status, elapsed=elapsed)
                await asyncio.sleep(poll_interval)

            # Fetch translated transcript
            translated_text = ""
            try:
                translated_text = await self._fetch_dubbing_transcript(
                    client, dubbing_id, target_language
                )
                logger.info("Translated transcript fetched",
                            text=translated_text[:200] if translated_text else "(empty)")
            except Exception as e:
                logger.warning("Failed to fetch translated transcript", error=str(e))

            # Fetch source transcript (for display in transcription window)
            source_text = ""
            try:
                source_text = await self._fetch_dubbing_transcript(
                    client, dubbing_id, source_language
                )
                logger.info("Source transcript fetched",
                            text=source_text[:200] if source_text else "(empty)")
            except Exception as e:
                logger.warning("Failed to fetch source transcript", error=str(e))

            # Download dubbed audio
            logger.info("Downloading dubbed audio", dubbing_id=dubbing_id, language=target_language)

            audio_response = await asyncio.to_thread(
                client.dubbing.audio.get,
                dubbing_id=dubbing_id,
                language_code=target_language
            )

            # Collect all chunks from the iterator
            dubbed_audio = b""
            for chunk in audio_response:
                dubbed_audio += chunk

            logger.info("Dubbed audio downloaded", dubbing_id=dubbing_id, size=len(dubbed_audio))

            # Clean up the dubbing project
            try:
                await asyncio.to_thread(client.dubbing.delete, dubbing_id)
                logger.debug("Dubbing project deleted", dubbing_id=dubbing_id)
            except Exception as e:
                logger.warning("Failed to delete dubbing project", dubbing_id=dubbing_id, error=str(e))

            return DubbingResult(audio=dubbed_audio, translated_text=translated_text, source_text=source_text)

        except Exception as e:
            logger.exception("Dubbing failed", error=str(e))
            return None

    @staticmethod
    def _parse_srt_text(srt_content: str) -> str:
        """Extract plain text from SRT subtitle content."""
        lines = srt_content.strip().split('\n')
        text_lines = []
        for line in lines:
            line = line.strip()
            # Skip empty lines, sequence numbers, and timestamps
            if not line or line.isdigit() or re.match(r'\d{2}:\d{2}:\d{2}', line):
                continue
            text_lines.append(line)
        return " ".join(text_lines)

    async def _fetch_dubbing_transcript(
        self, client: Any, dubbing_id: str, language_code: str
    ) -> str:
        """Fetch transcript text for a dubbed audio from the ElevenLabs API."""
        transcript_response = await asyncio.to_thread(
            client.dubbing.transcript.get_transcript_for_dub,
            dubbing_id=dubbing_id,
            language_code=language_code,
            format_type="srt",
        )

        if isinstance(transcript_response, str):
            return self._parse_srt_text(transcript_response)
        elif hasattr(transcript_response, 'utterances'):
            return " ".join(
                u.text.strip() for u in transcript_response.utterances
                if hasattr(u, 'text') and u.text and u.text.strip()
            )
        elif hasattr(transcript_response, 'srt') and transcript_response.srt:
            return self._parse_srt_text(transcript_response.srt)
        return ""

    async def translate_text_with_dubbing(
        self,
        text: str,
        target_language: str,
        source_language: str = "en",
    ) -> str:
        """
        Translate text using ElevenLabs Dubbing API.

        This is a workaround that:
        1. Synthesizes text to audio (English TTS)
        2. Dubs the audio to target language
        3. Transcribes the dubbed audio to get translated text

        Note: This is expensive and slow. For text-only translation,
        prefer OpenAI or other translation APIs.

        Args:
            text: Text to translate
            target_language: Target language code
            source_language: Source language code

        Returns:
            Translated text
        """
        # This method is a fallback - dubbing API works with audio, not text
        # For text translation, we still need an external service
        logger.warning("translate_text_with_dubbing called - this is inefficient, use translate_text instead")
        return text  # Passthrough for now

    async def list_voices(self) -> list[Voice]:
        """
        List all available voices.

        Returns:
            List of Voice objects
        """
        try:
            client = self._get_client()
            voices = client.voices.get_all()

            return [
                Voice(
                    voice_id=v.voice_id,
                    name=v.name,
                    category=v.category if hasattr(v, "category") else "unknown",
                    description=v.description if hasattr(v, "description") else None,
                )
                for v in voices.voices
            ]

        except Exception as e:
            logger.exception("Failed to list voices", error=str(e))
            raise

    async def delete_voice(self, voice_id: str) -> bool:
        """
        Delete a voice.

        Args:
            voice_id: Voice ID to delete

        Returns:
            True if successful
        """
        try:
            client = self._get_client()
            client.voices.delete(voice_id=voice_id)
            logger.info("Voice deleted", voice_id=voice_id)
            return True

        except Exception as e:
            logger.exception("Failed to delete voice", voice_id=voice_id, error=str(e))
            return False

    def is_language_supported(self, language_code: str) -> bool:
        """Check if a language is supported."""
        return language_code in self.SUPPORTED_LANGUAGES or language_code == "auto"

    def get_supported_languages(self) -> dict[str, str]:
        """Get dictionary of supported languages."""
        return self.SUPPORTED_LANGUAGES.copy()
