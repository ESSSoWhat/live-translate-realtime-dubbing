"""Health check endpoint tests."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.dependencies import get_current_user


def test_health_returns_ok(client: TestClient) -> None:
    """GET /health returns status ok."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_translate_uses_google_endpoint_when_openai_is_unavailable(client: TestClient) -> None:
    """POST /proxy/translate returns a real translation instead of echoing the source text."""
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "pro"}
    google = AsyncMock(return_value="見つけました。")
    try:
        with (
            patch("app.routers.proxy.check_and_record_quota", AsyncMock()),
            patch("app.routers.proxy.get_settings", return_value=MagicMock(openai_api_key="")),
            patch("app.routers.proxy._google_translate", google),
        ):
            r = client.post(
                "/api/v1/proxy/translate",
                json={"text": "We found it.", "target_language": "ja", "source_language": "en"},
            )
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["translated_text"] == "見つけました。"
    google.assert_awaited_once_with("We found it.", "ja")
