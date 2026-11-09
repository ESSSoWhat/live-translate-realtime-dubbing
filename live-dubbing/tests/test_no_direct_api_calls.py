"""
Test that production code never makes direct calls to OpenAI or ElevenLabs APIs.

All API calls must go through the backend proxy (/api/v1/proxy/*) by default.
Direct calls are only allowed with explicit LT_DEV_DIRECT_API=1 env var.
"""

import os
import pytest
from unittest.mock import Mock, patch, AsyncMock
import httpx


# Forbidden domains - production code must never call these directly
FORBIDDEN_DOMAINS = [
    "api.openai.com",
    "api.elevenlabs.io",
]


def check_no_forbidden_calls(url: str) -> None:
    """Raise assertion if URL points to a forbidden domain."""
    for domain in FORBIDDEN_DOMAINS:
        if domain in url:
            raise AssertionError(
                f"Direct API call detected to {domain}!\n"
                f"URL: {url}\n"
                f"Production code must use backend proxy (/api/v1/proxy/*), "
                f"not direct provider calls."
            )


class HTTPInterceptor:
    """Mock HTTP client that tracks all outgoing requests and blocks forbidden domains."""
    
    def __init__(self):
        self.requests = []
        self.forbidden_calls = []
    
    async def request(self, method: str, url: str, **kwargs):
        """Intercept HTTP request and check for forbidden domains."""
        full_url = url if url.startswith("http") else f"http://mock{url}"
        self.requests.append((method, full_url))
        
        # Check for forbidden domains
        for domain in FORBIDDEN_DOMAINS:
            if domain in full_url:
                self.forbidden_calls.append((method, full_url))
                raise AssertionError(
                    f"BLOCKED: Direct call to {domain} detected!\n"
                    f"{method} {full_url}\n"
                    f"This violates the requirement that all provider calls "
                    f"go through /api/v1/proxy/*"
                )
        
        # Return mock response for allowed calls
        mock_response = Mock()
        mock_response.status_code = 200
        
        # Return appropriate JSON based on endpoint
        if "/transcribe" in full_url:
            mock_response.json = Mock(return_value={
                "text": "mock transcription",
                "language_code": "en",
                "confidence": 0.9
            })
        elif "/translate" in full_url:
            mock_response.json = Mock(return_value={
                "translated_text": "mock translation",
                "source_language": "en",
                "target_language": "es"
            })
        elif "/synthesize" in full_url:
            mock_response.content = b"mock_audio_data"
        elif "/clone-voice" in full_url:
            mock_response.json = Mock(return_value={"voice_id": "mock_voice_123"})
        else:
            mock_response.json = Mock(return_value={"text": "mock", "voice_id": "mock_voice"})
        
        mock_response.content = b"mock_audio"
        return mock_response
    
    async def get(self, url: str, **kwargs):
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs):
        return await self.request("POST", url, **kwargs)


@pytest.mark.asyncio
async def test_backend_proxy_service_uses_proxy_only():
    """Verify BackendProxyService never calls OpenAI or ElevenLabs directly."""
    from live_dubbing.services.backend_service import BackendProxyService
    
    interceptor = HTTPInterceptor()
    
    # Create service with mock HTTP client
    service = BackendProxyService(
        base_url="http://localhost:8765",
        access_token="mock_token",
        refresh_token="mock_refresh",
    )
    service._client = interceptor
    
    # Test transcribe
    await service.transcribe(b"fake_audio", language="en")
    
    # Test translate
    await service.translate_text("test", "es", "en")
    
    # Test synthesize
    await service.synthesize("test", "voice_id")
    
    # Verify all calls went through backend proxy, not direct to providers
    assert len(interceptor.requests) > 0, "Expected HTTP requests"
    assert len(interceptor.forbidden_calls) == 0, (
        f"Found {len(interceptor.forbidden_calls)} forbidden direct API calls:\n" +
        "\n".join(f"{m} {u}" for m, u in interceptor.forbidden_calls)
    )
    
    # Verify all calls are to backend endpoints
    for method, url in interceptor.requests:
        assert "/api/v1/proxy/" in url or "/api/v1/user/" in url or "/api/v1/auth/" in url, (
            f"Expected backend proxy endpoint, got: {method} {url}"
        )


@pytest.mark.asyncio
async def test_direct_api_requires_dev_flag():
    """Verify direct API mode requires explicit LT_DEV_DIRECT_API=1."""
    from live_dubbing.core.orchestrator import Orchestrator
    from live_dubbing.core.events import EventBus
    from live_dubbing.config.settings import AppSettings
    
    settings = AppSettings()
    settings._elevenlabs_api_key = "test_key"
    settings._access_token = None  # Not signed in
    
    event_bus = EventBus()
    orchestrator = Orchestrator(settings, event_bus)
    
    # Should raise error when not signed in and dev flag not set
    with pytest.raises(RuntimeError, match="Authentication required"):
        await orchestrator._init_elevenlabs()


@pytest.mark.asyncio
async def test_dev_flag_allows_direct_api():
    """Verify LT_DEV_DIRECT_API=1 enables direct API mode."""
    from live_dubbing.core.orchestrator import Orchestrator
    from live_dubbing.core.events import EventBus
    from live_dubbing.config.settings import AppSettings
    
    # Set dev flag
    old_val = os.environ.get("LT_DEV_DIRECT_API")
    os.environ["LT_DEV_DIRECT_API"] = "1"
    
    try:
        settings = AppSettings()
        settings._elevenlabs_api_key = "test_key"
        settings._access_token = None
        
        event_bus = EventBus()
        orchestrator = Orchestrator(settings, event_bus)
        await orchestrator._init_elevenlabs()
        
        # Should have created ElevenLabsService (direct API)
        from live_dubbing.services.elevenlabs_service import ElevenLabsService
        assert isinstance(orchestrator._elevenlabs_service, ElevenLabsService)
        
    finally:
        # Restore env
        if old_val is None:
            os.environ.pop("LT_DEV_DIRECT_API", None)
        else:
            os.environ["LT_DEV_DIRECT_API"] = old_val


@pytest.mark.asyncio
async def test_signed_in_uses_backend_proxy():
    """Verify signed-in users always use backend proxy (even with local API key)."""
    from live_dubbing.core.orchestrator import Orchestrator
    from live_dubbing.core.events import EventBus
    from live_dubbing.config.settings import AppSettings
    
    # Ensure dev flag is NOT set
    old_val = os.environ.get("LT_DEV_DIRECT_API")
    os.environ.pop("LT_DEV_DIRECT_API", None)
    
    try:
        settings = AppSettings()
        settings._elevenlabs_api_key = "local_key"  # Has local key
        settings._access_token = "valid_token"  # But also signed in
        settings._refresh_token = "refresh"
        
        event_bus = EventBus()
        orchestrator = Orchestrator(settings, event_bus)
        await orchestrator._init_elevenlabs()
        
        # Should use backend proxy, not direct API
        from live_dubbing.services.backend_service import BackendProxyService
        assert isinstance(orchestrator._elevenlabs_service, BackendProxyService)
        
    finally:
        # Restore env
        if old_val is not None:
            os.environ["LT_DEV_DIRECT_API"] = old_val


def test_no_env_file_in_production():
    """Verify .env files are never bundled in production builds."""
    import sys
    from pathlib import Path
    
    # Check if running from PyInstaller bundle
    if getattr(sys, 'frozen', False):
        # Running from bundle
        app_dir = Path(sys.executable).parent
        
        # Check for .env in various locations
        forbidden_env_files = [
            app_dir / ".env",
            app_dir / "_internal" / ".env",
            app_dir / "live_dubbing" / ".env",
        ]
        
        for env_file in forbidden_env_files:
            assert not env_file.exists(), (
                f"Found .env file in production bundle: {env_file}\n"
                f"Secrets must never be bundled in the installer!"
            )


def test_readme_documents_dev_flag():
    """Verify dev flag is documented in README."""
    from pathlib import Path
    
    readme_path = Path(__file__).parent.parent / "README.md"
    if readme_path.exists():
        content = readme_path.read_text(encoding="utf-8")
        assert "LT_DEV_DIRECT_API" in content, (
            "LT_DEV_DIRECT_API env var must be documented in README"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
