"""Unit tests for Supabase JWT verification routing (JWKS RS256/ES256 + HS256 fallback).

No network: RS256 is tested with a locally-generated RSA keypair and a mocked PyJWKClient.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import dependencies as deps

ISSUER = "https://test.supabase.co/auth/v1"  # matches conftest SUPABASE_URL
AUD = "authenticated"
HS_SECRET = "test-jwt-secret"  # matches conftest SUPABASE_JWT_SECRET

_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pub_pem = _priv.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)
_priv_pem = _priv.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def _claims(sub: str = "user-abc", **over) -> dict:
    now = dt.datetime.now(tz=dt.timezone.utc)
    c = {"sub": sub, "iss": ISSUER, "aud": AUD, "exp": now + dt.timedelta(hours=1), "iat": now}
    c.update(over)
    return c


def _rs256(claims: dict) -> str:
    return jwt.encode(claims, _priv_pem, algorithm="RS256", headers={"kid": "test-kid"})


def _hs256(claims: dict) -> str:
    return jwt.encode(claims, HS_SECRET, algorithm="HS256")


def _mock_jwks():
    """Patch _get_jwks_client so RS256 verification uses our local public key."""
    fake = MagicMock()
    fake.get_signing_key_from_jwt.return_value = SimpleNamespace(key=_pub_pem)
    return patch.object(deps, "_get_jwks_client", return_value=fake)


def test_rs256_valid_returns_sub() -> None:
    with _mock_jwks():
        assert deps._verify_supabase_jwt(_rs256(_claims("uid-1"))) == "uid-1"


def test_rs256_tampered_rejected() -> None:
    tok = _rs256(_claims("uid-1"))
    tampered = tok[:-3] + ("aaa" if not tok.endswith("aaa") else "bbb")
    with _mock_jwks(), pytest.raises(HTTPException) as e:
        deps._verify_supabase_jwt(tampered)
    assert e.value.status_code == 401


def test_rs256_wrong_issuer_rejected() -> None:
    with _mock_jwks(), pytest.raises(HTTPException) as e:
        deps._verify_supabase_jwt(_rs256(_claims("uid-1", iss="https://evil.supabase.co/auth/v1")))
    assert e.value.status_code == 401


def test_rs256_expired_rejected() -> None:
    past = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=2)
    with _mock_jwks(), pytest.raises(HTTPException) as e:
        deps._verify_supabase_jwt(_rs256(_claims("uid-1", exp=past, iat=past)))
    assert e.value.status_code == 401


def test_hs256_fallback_valid_returns_sub() -> None:
    assert deps._verify_supabase_jwt(_hs256(_claims("uid-hs"))) == "uid-hs"


def test_hs256_without_secret_rejected() -> None:
    cfg = SimpleNamespace(supabase_url="https://test.supabase.co", supabase_jwt_secret="")
    with patch.object(deps, "get_settings", return_value=cfg), pytest.raises(HTTPException) as e:
        deps._verify_supabase_jwt(_hs256(_claims("uid-hs")))
    assert e.value.status_code == 401


def test_unsupported_alg_rejected() -> None:
    tok = jwt.encode(_claims("uid-x"), key="", algorithm="none")
    with pytest.raises(HTTPException) as e:
        deps._verify_supabase_jwt(tok)
    assert e.value.status_code == 401


def test_looks_like_jwt() -> None:
    assert deps._looks_like_jwt(_hs256(_claims())) is True
    assert deps._looks_like_jwt("plain-api-key-token-no-dots") is False
