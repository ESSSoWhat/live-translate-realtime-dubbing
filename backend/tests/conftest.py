"""Pytest fixtures and env for backend tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set minimal env so get_settings() does not fail when app is imported
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-elevenlabs-key")

from app.main import create_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """FastAPI test client."""
    app = create_app()
    with TestClient(app) as c:
        yield c


def _make_session(access_token: str = "access", refresh_token: str = "refresh", expires_in: int = 3600) -> MagicMock:
    s = MagicMock()
    s.access_token = access_token
    s.refresh_token = refresh_token
    s.expires_in = expires_in
    return s


def _make_user(uid: str = "supa-uid-1", email: str = "user@test.com") -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.email = email
    return u


def _usage_snapshot() -> dict:
    return {
        "dubbing_seconds_used": 0,
        "dubbing_seconds_limit": 1800,
        "tts_chars_used": 0,
        "tts_chars_limit": 50000,
        "stt_seconds_used": 0,
        "stt_seconds_limit": 3600,
        "voice_clones_used": 0,
        "voice_clones_limit": 1,
        "period_reset_date": "2025-02-01",
    }


@pytest.fixture
def mock_supabase() -> Generator[MagicMock, None, None]:
    """Provide a mock Supabase client for auth tests."""
    sb = MagicMock()

    # Auth responses
    sb.auth.sign_up = AsyncMock(
        return_value=MagicMock(
            user=_make_user("supa-1", "new@test.com"),
            session=_make_session("at-new", "rt-new"),
        )
    )
    sb.auth.sign_in_with_password = AsyncMock(
        return_value=MagicMock(
            user=_make_user("supa-1", "user@test.com"),
            session=_make_session("at-login", "rt-login"),
        )
    )
    sb.auth.refresh_session = AsyncMock(
        return_value=MagicMock(
            session=_make_session("at-refreshed", "rt-refreshed"),
        )
    )
    sb.auth.admin.delete_user = AsyncMock(return_value=None)

    # Table chain: .table("users").select("*").eq(...).maybe_single().execute() -> .data = row dict
    # and .table("users").insert({...}).execute() -> .data = [row]
    user_row = {"id": 1, "supabase_uid": "supa-1", "email": "user@test.com", "tier": "free"}

    def table_chain(*args: object, **kwargs: object) -> MagicMock:
        chain = MagicMock()
        # select path (login, oauth): returns single row
        chain.select = MagicMock(return_value=chain)
        chain.eq = MagicMock(return_value=chain)
        chain.maybe_single = MagicMock(
            return_value=MagicMock(
                execute=AsyncMock(return_value=MagicMock(data=user_row))
            )
        )
        # insert path (register): returns [row]
        chain.insert = MagicMock(
            return_value=MagicMock(
                execute=AsyncMock(return_value=MagicMock(data=[user_row]))
            )
        )
        return chain

    sb.table = MagicMock(side_effect=table_chain)
    return sb


@pytest.fixture
def auth_client(
    mock_supabase: MagicMock,
) -> Generator[TestClient, None, None]:
    """Test client with mocked Supabase and get_usage_snapshot for auth routes."""
    async def fake_usage_snapshot(user_id: str) -> dict:
        return _usage_snapshot()

    with patch("app.routers.auth.get_supabase", AsyncMock(return_value=mock_supabase)):
        with patch("app.routers.auth.get_usage_snapshot", side_effect=fake_usage_snapshot):
            app = create_app()
            with TestClient(app) as c:
                yield c
