"""
BackendProxyService — drop-in replacement for ElevenLabsService.

Instead of calling ElevenLabs/OpenAI directly, every method proxies
through the Live Translate backend API. API keys never leave the server.

Public method signatures are intentionally identical to ElevenLabsService
so the orchestrator, pipeline, and voice manager need zero changes.
"""

from __future__ import annotations

import asyncio
import json
import io
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Callable

import httpx
import structlog

from live_dubbing.services.elevenlabs_service import TranscriptionResult

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

BACKEND_BASE_URL_DEFAULT = "https://livetranslatedubtool-production.up.railway.app"  # override via env


class AuthExpiredException(Exception):
    """Raised when the backend returns 401 and token refresh has failed."""


class QuotaExceededException(Exception):
    """Raised when the backend returns 402 (quota exceeded)."""

    def __init__(self, event_type: str, upgrade_url: str = "") -> None:
        self.event_type = event_type
        self.upgrade_url = upgrade_url
        super().__init__(f"Quota exceeded for {event_type}")


class BackendProxyService:
    """
    Proxy service that forwards all AI calls to the Live Translate backend.

    Mirrors the public interface of ElevenLabsService:
      - transcribe(audio_bytes, language) -> str
      - translate_text(text, target_language, source_language) -> str
      - synthesize(text, voice_id, ...) -> bytes
      - synthesize_stream(text, voice_id, ...) -> AsyncIterator[bytes]
      - clone_voice(audio_data, name, description) -> str (voice_id)
      - list_voices() -> list[dict]
      - delete_voice(voice_id) -> None
    """

    def __init__(
        self,
        base_url: str,
        access_token: str,
        refresh_token: str,
        on_token_refreshed: Callable[[str, str], None] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._on_token_refreshed = on_token_refreshed
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=60.0,
            headers={"User-Agent": "LiveTranslate-Desktop/1.0"},
        )

    # ── OAuth helpers (no auth required — called before login) ───────────────

    @staticmethod
    def fetch_google_oauth_url(base_url: str, redirect_uri: str) -> str:
        """
        Synchronously fetch the Google OAuth URL from the backend.

        This is a *static* helper so it can be called before any tokens exist.
        Uses a plain ``httpx.Client`` (no bearer header).

        Args:
            base_url:      Backend root URL (e.g. ``https://api.livetranslate.net``).
            redirect_uri:  The local callback URL to embed in the OAuth request
                           (e.g. ``http://localhost:8821/``).

        Returns:
            The full Google OAuth redirect URL.

        Raises:
            httpx.HTTPStatusError: If the backend returns a non-2xx response.
            KeyError: If the backend response does not contain ``"url"``.
        """
        url = base_url.rstrip("/") + "/api/v1/auth/oauth/google"
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params={"redirect_uri": redirect_uri})
            response.raise_for_status()
            return str(response.json()["url"])

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _refresh_access_token(self) -> None:
        """Exchange refresh token for a new access token and update internal state."""
        current_refresh = self._refresh_token
        async with self._refresh_lock:
            if self._refresh_token != current_refresh:
                return
            response = await self._client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": self._refresh_token},
            )
            if response.status_code != 200:
                raise AuthExpiredException("Session expired — please log in again")
            data = response.json()
            self._access_token = data["access_token"]
            self._refresh_token = data["refresh_token"]
            if self._on_token_refreshed:
                self._on_token_refreshed(self._access_token, self._refresh_token)
            logger.debug("Access token refreshed silently")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retry_on_401: bool = True,
        **kwargs,
    ) -> httpx.Response:
        """Make an authenticated request, retrying once after token refresh on 401."""
        kwargs.setdefault("headers", {}).update(self._auth_headers())
        response = await self._client.request(method, path, **kwargs)

        # Only attempt refresh when we have a refresh token (JWT flow); API key has none
        if response.status_code == 401 and retry_on_401 and (self._refresh_token or "").strip():
            try:
                await self._refresh_access_token()
                kwargs["headers"].update(self._auth_headers())
                response = await self._client.request(method, path, **kwargs)
            except AuthExpiredException:
                pass  # Re-raise after we check status below

        if response.status_code == 402:
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                body = {}
            detail = body.get("detail", body) if isinstance(body, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            raise QuotaExceededException(
                event_type=detail.get("event_type", "unknown"),
                upgrade_url=detail.get("upgrade_url", ""),
            )

        if response.status_code == 401:
            raise AuthExpiredException("Session expired — please log in again")

        response.raise_for_status()
        return response

    # ── STT ──────────────────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        """Transcribe audio bytes to text via the backend proxy."""
        response = await self._request(
            "POST",
            "/api/v1/proxy/transcribe",
            files={"audio": ("audio.wav", io.BytesIO(audio_data), "audio/wav")},
            data={"language": language, "sample_rate": str(sample_rate)},
            headers={},  # let _request add auth
        )
        data = response.json()
        return TranscriptionResult(
            text=data.get("text", ""),
            language_code=data.get("language_code", language if language != "auto" else "en"),
            confidence=data.get("confidence", 0.9),
            is_final=True,
        )

    # ── Translation ──────────────────────────────────────────────────────────

    async def translate_text(
        self,
        text: str,
        target_language: str,
        source_language: str = "auto",
        context: str = "",
    ) -> str:
        """Translate text via the backend proxy."""
        if not text.strip():
            return text
        response = await self._request(
            "POST",
            "/api/v1/proxy/translate",
            json={
                "text": text,
                "target_language": target_language,
                "source_language": source_language,
                "context": context,
            },
            headers={},
        )
        return response.json()["translated_text"]

    # ── TTS ──────────────────────────────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        output_format: str = "mp3_44100_128",
    ) -> bytes:
        """Synthesize text to audio bytes via the backend proxy."""
        response = await self._request(
            "POST",
            "/api/v1/proxy/synthesize",
            json={
                "text": text,
                "voice_id": voice_id,
                "model_id": model_id,
                "stability": stability,
                "similarity_boost": similarity_boost,
                "output_format": output_format,
            },
            headers={},
        )
        return response.content

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
    ) -> AsyncIterator[bytes]:
        """Stream synthesized audio chunks from the backend proxy."""
        async def _stream() -> AsyncIterator[bytes]:
            async with self._client.stream(
                "POST",
                "/api/v1/proxy/synthesize/stream",
                json={
                    "text": text,
                    "voice_id": voice_id,
                    "model_id": model_id,
                    "stability": stability,
                    "similarity_boost": similarity_boost,
                },
                headers=self._auth_headers(),
            ) as response:
                if response.status_code == 401:
                    await self._refresh_access_token()
                    async with self._client.stream(
                        "POST",
                        "/api/v1/proxy/synthesize/stream",
                        json={
                            "text": text,
                            "voice_id": voice_id,
                            "model_id": model_id,
                            "stability": stability,
                            "similarity_boost": similarity_boost,
                        },
                        headers=self._auth_headers(),
                    ) as retry_response:
                        if retry_response.status_code == 402:
                            body = await retry_response.aread()
                            try:
                                raw = json.loads(body.decode())
                                detail = raw.get("detail", {}) if isinstance(raw, dict) else {}
                            except (ValueError, json.JSONDecodeError):
                                detail = {}
                            if not isinstance(detail, dict):
                                detail = {}
                            raise QuotaExceededException(
                                event_type=detail.get("event_type", "tts"),
                                upgrade_url=detail.get("upgrade_url", ""),
                            )
                        if retry_response.status_code == 401:
                            raise AuthExpiredException("Session expired — please log in again")
                        retry_response.raise_for_status()
                        async for chunk in retry_response.aiter_bytes(chunk_size=4096):
                            yield chunk
                    return
                if response.status_code == 402:
                    body = await response.aread()
                    try:
                        raw = json.loads(body.decode())
                        detail = raw.get("detail", {}) if isinstance(raw, dict) else {}
                    except (ValueError, json.JSONDecodeError):
                        detail = {}
                    if not isinstance(detail, dict):
                        detail = {}
                    raise QuotaExceededException(
                        event_type=detail.get("event_type", "tts"),
                        upgrade_url=detail.get("upgrade_url", ""),
                    )
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    yield chunk
        async for chunk in _stream():
            yield chunk

    # ── Voice management ─────────────────────────────────────────────────────

    async def clone_voice(
        self,
        audio_data: bytes,
        name: str,
        description: str | None = None,
        filename: str = "audio.wav",
    ) -> str:
        """Clone a voice and return the new voice_id."""
        response = await self._request(
            "POST",
            "/api/v1/proxy/clone-voice",
            files={"audio": (filename, io.BytesIO(audio_data), "audio/wav")},
            data={"name": name, "description": description or ""},
            headers={},
        )
        return response.json()["voice_id"]

    async def clone_voice_from_file(
        self,
        file_path: str,
        name: str,
        description: str | None = None,
    ) -> str:
        """Clone a voice from an audio file."""
        import os

        with open(file_path, "rb") as f:
            audio_data = f.read()
        filename = os.path.basename(file_path) or "audio.wav"
        return await self.clone_voice(
            audio_data=audio_data,
            name=name,
            description=description,
            filename=filename,
        )

    async def list_voices(self) -> list[dict]:
        """Return all available voices."""
        response = await self._request("GET", "/api/v1/proxy/voices", headers={})
        return response.json()

    async def delete_voice(self, voice_id: str) -> None:
        """Delete a cloned voice."""
        await self._request("DELETE", f"/api/v1/proxy/voices/{voice_id}", headers={})

    # ── Cleanup ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    # ── Compatibility shims (methods referenced by orchestrator/pipeline) ────

    @property
    def is_configured(self) -> bool:
        """Always True — token presence means service is configured."""
        return bool(self._access_token)

    async def get_usage(self) -> dict:
        """Fetch current usage snapshot for the logged-in user."""
        response = await self._request("GET", "/api/v1/user/usage", headers={})
        return response.json()
