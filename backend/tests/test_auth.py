"""Auth endpoint tests with mocked Supabase and usage."""

import pytest
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
