"""Auth endpoint tests with mocked Supabase and usage."""

from __future__ import annotations

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def test_register_returns_tokens(auth_client: TestClient) -> None:
    """POST /api/v1/auth/register returns access and refresh tokens."""
    r = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "new@test.com", "password": "password123"},
    )
    assert r.status_code == 201
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["email"] == "new@test.com"
    assert data["user_id"] == "1"
    assert data["tier"] == "free"
    assert "usage" in data


def test_login_returns_tokens(auth_client: TestClient) -> None:
    """POST /api/v1/auth/login returns access and refresh tokens."""
    r = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "user@test.com", "password": "secret"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["access_token"] == "at-login"
    assert data["refresh_token"] == "rt-login"
    assert data["email"] == "user@test.com"
    assert "usage" in data


def test_refresh_returns_new_tokens(auth_client: TestClient) -> None:
    """POST /api/v1/auth/refresh returns new access and refresh tokens."""
    r = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "rt-login"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["access_token"] == "at-refreshed"
    assert data["refresh_token"] == "rt-refreshed"


def test_login_missing_password_returns_422(auth_client: TestClient) -> None:
    """POST /api/v1/auth/login without password returns validation error."""
    r = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "user@test.com"},
    )
    assert r.status_code == 422


def _authorize_query(url: str) -> dict[str, list[str]]:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.parse_qs(parsed.query)


def test_google_oauth_url_localhost_sets_pkce_flow(client: TestClient) -> None:
    """Localhost callbacks still advertise PKCE (desktop injects the challenge)."""
    r = client.get(
        "/api/v1/auth/oauth/google",
        params={"redirect_uri": "http://127.0.0.1:18765/"},
    )
    assert r.status_code == 200
    qs = _authorize_query(r.json()["url"])
    assert qs["provider"] == ["google"]
    assert qs["redirect_to"] == ["http://127.0.0.1:18765/"]
    assert qs["flow_type"] == ["pkce"]
    assert "code_challenge" not in qs


def test_google_oauth_url_https_bridge_omits_bare_pkce(client: TestClient) -> None:
    """HTTPS desktop-sso must not use flow_type=pkce without a challenge."""
    r = client.get(
        "/api/v1/auth/oauth/google",
        params={"redirect_uri": "https://api.example.com/api/v1/auth/desktop-sso"},
    )
    assert r.status_code == 200
    qs = _authorize_query(r.json()["url"])
    assert "flow_type" not in qs
    assert qs["redirect_to"] == ["https://api.example.com/api/v1/auth/desktop-sso"]


def test_google_oauth_url_strips_query_from_https_redirect(client: TestClient) -> None:
    """Query params in redirect_to make Google return 500 / 1500."""
    r = client.get(
        "/api/v1/auth/oauth/google",
        params={
            "redirect_uri": (
                "https://api.example.com/api/v1/auth/desktop-sso"
                "?session_id=abc1234567890123"
                "&redirect_uri=http://localhost:64230/"
            ),
            "code_challenge": "abcChallenge",
        },
    )
    assert r.status_code == 200
    qs = _authorize_query(r.json()["url"])
    assert qs["redirect_to"] == ["https://api.example.com/api/v1/auth/desktop-sso"]
    assert "session_id" not in qs["redirect_to"][0]
    assert "localhost" not in qs["redirect_to"][0]


def test_google_oauth_url_includes_code_challenge(client: TestClient) -> None:
    """Bridge PKCE challenge is forwarded onto the Supabase authorize URL."""
    r = client.get(
        "/api/v1/auth/oauth/google",
        params={
            "redirect_uri": "https://api.example.com/api/v1/auth/desktop-sso",
            "code_challenge": "abcChallenge",
        },
    )
    assert r.status_code == 200
    qs = _authorize_query(r.json()["url"])
    assert qs["code_challenge"] == ["abcChallenge"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "flow_type" not in qs


def test_desktop_sso_page_handles_pkce_code(client: TestClient) -> None:
    """SSO HTML exchanges ?code= and still offers a return-to-app link."""
    r = client.get("/api/v1/auth/desktop-sso")
    assert r.status_code == 200
    body = r.text
    assert "oauth_code" in body
    assert "code_challenge" in body
    assert "lt_pkce" in body
    assert "window.location.replace(appUrl)" in body
    assert "function isSafeDesktopRedirect" in body
    assert "var bridgeUrl = window.location.origin + window.location.pathname;" in body


def test_desktop_sso_complete_oauth_code_stores_handoff(
    auth_client: TestClient, mock_supabase: MagicMock
) -> None:
    """PKCE code from Google is exchanged, then the app polls handoff."""
    session = MagicMock()
    session.access_token = "google-at"
    mock_supabase.auth.exchange_code_for_session = AsyncMock(
        return_value=MagicMock(session=session, user=MagicMock(id="supa-1", email="user@test.com"))
    )
    with (
        patch("app.dependencies._verify_supabase_jwt", return_value="supa-1"),
        patch("app.routers.auth.create_auth_client", AsyncMock(return_value=mock_supabase)),
        patch("app.services.desktop_handoff.put_handoff", new_callable=AsyncMock) as put,
        patch(
            "app.routers.auth._ensure_user_api_key",
            new_callable=AsyncMock,
            return_value="lt_api_key",
        ),
    ):
        r = auth_client.post(
            "/api/v1/auth/desktop-sso/complete",
            json={
                "session_id": "desktop-session-id-24chars",
                "redirect_uri": "http://127.0.0.1:18765/",
                "oauth_code": "auth-code",
                "code_verifier": "verifier",
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["api_key"] == "lt_api_key"
    assert data["redirect"].startswith("http://127.0.0.1:18765/?api_key=")
    put.assert_awaited_once()
    exchanged = mock_supabase.auth.exchange_code_for_session.await_args
    assert exchanged is not None
    assert exchanged.args[0]["auth_code"] == "auth-code"
    assert exchanged.args[0]["code_verifier"] == "verifier"


def test_desktop_sso_complete_returns_key_when_handoff_store_fails(
    auth_client: TestClient, mock_supabase: MagicMock
) -> None:
    """Handoff RLS failures must not hide the API key from the SSO page."""
    session = MagicMock()
    session.access_token = "google-at"
    mock_supabase.auth.exchange_code_for_session = AsyncMock(
        return_value=MagicMock(session=session, user=MagicMock(id="supa-1", email="user@test.com"))
    )
    with (
        patch("app.dependencies._verify_supabase_jwt", return_value="supa-1"),
        patch("app.routers.auth.create_auth_client", AsyncMock(return_value=mock_supabase)),
        patch(
            "app.services.desktop_handoff.put_handoff",
            new_callable=AsyncMock,
            side_effect=RuntimeError("rls"),
        ),
        patch(
            "app.routers.auth._ensure_user_api_key",
            new_callable=AsyncMock,
            return_value="lt_api_key",
        ),
    ):
        r = auth_client.post(
            "/api/v1/auth/desktop-sso/complete",
            json={
                "session_id": "desktop-session-id-24chars",
                "redirect_uri": "http://127.0.0.1:18765/",
                "oauth_code": "auth-code",
                "code_verifier": "verifier",
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["api_key"] == "lt_api_key"
    assert "api_key=" in data["redirect"]


def test_google_id_token_login_returns_tokens(auth_client: TestClient) -> None:
    """POST /auth/oauth/google/id-token (mobile SDK) returns a session."""
    r = auth_client.post(
        "/api/v1/auth/oauth/google/id-token",
        json={"id_token": "fake-google-id-token"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["access_token"] == "at-google"
    assert data["refresh_token"] == "rt-google"
    assert data["email"] == "user@test.com"
    assert "usage" in data


def test_google_id_token_creates_user_when_lookup_empty(
    auth_client: TestClient, mock_supabase: MagicMock
) -> None:
    """Empty users lookup must insert instead of raising Internal server error."""
    created = {
        "id": "new-1",
        "supabase_uid": "supa-1",
        "email": "user@test.com",
        "tier": "free",
    }

    def table_chain(*args: object, **kwargs: object) -> MagicMock:
        chain = MagicMock()
        chain.select = MagicMock(return_value=chain)
        chain.eq = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.update = MagicMock(return_value=chain)
        chain.execute = AsyncMock(return_value=MagicMock(data=[]))
        chain.insert = MagicMock(
            return_value=MagicMock(
                execute=AsyncMock(return_value=MagicMock(data=[created]))
            )
        )
        return chain

    mock_supabase.table = MagicMock(side_effect=table_chain)
    r = auth_client.post(
        "/api/v1/auth/oauth/google/id-token",
        json={"id_token": "fake-google-id-token"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "new-1"
