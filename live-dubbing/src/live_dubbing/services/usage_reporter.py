"""Usage reporter for direct API mode — reports usage to backend when signed in."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

VALID_EVENT_TYPES = frozenset({"stt", "tts", "dub", "translate", "clone"})


class UsageReporter:
    """Fire-and-forget usage reporting to backend (for direct ElevenLabs mode)."""

    def __init__(self, base_url: str, access_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = access_token
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _client_or_new(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=10.0,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "User-Agent": "LiveTranslate-Desktop/1.0",
                    },
                )
            return self._client

    async def aclose(self) -> None:
        """Close the HTTP client and release resources."""
        async with self._client_lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    def report(self, event_type: str, quantity: int) -> None:
        """Report usage asynchronously; does not block. Ignores errors."""
        if event_type not in VALID_EVENT_TYPES or quantity <= 0:
            return
        asyncio.create_task(self._report_async(event_type, quantity))

    async def _report_async(self, event_type: str, quantity: int) -> None:
        """POST usage to backend; log and discard on failure."""
        try:
            client = await self._client_or_new()
            resp = await client.post(
                "/api/v1/user/usage/report",
                json={"event_type": event_type, "quantity": quantity},
            )
            if resp.status_code != 200:
                logger.debug(
                    "Usage report failed",
                    event_type=event_type,
                    quantity=quantity,
                    status=resp.status_code,
                )
        except Exception as exc:
            logger.debug("Usage report failed", event_type=event_type, error=str(exc))
