"""FastAPI dependencies — JWT (Supabase JWKS/HS256) or API key auth, current user."""

from __future__ import annotations

import logging

import jwt  # PyJWT
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient

from app.config import get_settings
from app.services.supabase_client import create_auth_client

logger = logging.getLogger(__name__)

# Cached PyJWKClient (fetches + caches Supabase's asymmetric signing keys, handles rotation).
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Return a cached PyJWKClient for the project's JWKS endpoint."""
    global _jwks_client  # pylint: disable=global-statement
    if _jwks_client is None:
        base = get_settings().supabase_url.rstrip("/")
        _jwks_client = PyJWKClient(
            f"{base}/auth/v1/.well-known/jwks.json",
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=3600,
        )
    return _jwks_client


def _looks_like_jwt(token: str) -> bool:
    """True if token has three dot-separated parts (JWT shape)."""
    return token.count(".") == 2 and len(token) > 20


def _rows(result: object) -> list[dict]:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _jwt_email(token: str) -> str:
    """Read the email claim from an already-verified access token."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return ""
    return str(claims.get("email") or "")


def _verify_supabase_jwt(token: str) -> str:
    """Verify a Supabase access token (asymmetric via JWKS, or legacy HS256) and return the 'sub'.

    Raises HTTPException(401) on any validation failure.
    """
    cfg = get_settings()
    base = cfg.supabase_url.rstrip("/")
    issuer = f"{base}/auth/v1"
    audience = "authenticated"

    try:
        alg = (jwt.get_unverified_header(token) or {}).get("alg")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header") from exc

    try:
        if alg in ("RS256", "ES256"):
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key, algorithms=[alg], audience=audience, issuer=issuer
            )
        elif alg == "HS256":
            secret = (cfg.supabase_jwt_secret or "").strip()
            if not secret:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="HS256 token but SUPABASE_JWT_SECRET is not configured",
                )
            payload = jwt.decode(
                token, secret, algorithms=["HS256"], audience=audience, issuer=issuer
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Unsupported token algorithm: {alg}"
            )
    except HTTPException:
        raise
    except jwt.PyJWKClientError as exc:
        logger.warning("JWKS fetch failed", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to fetch signing key"
        ) from exc
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim")
    return sub


async def get_current_user(authorization: str | None = Header(default=None)) -> dict:  # noqa: B008
    """
    Validate Bearer token: either a backend API key or a Supabase JWT.
    Returns the user row (id, email, tier, subscription_status, ...).
    Raises HTTP 401 if token is missing or invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    # A fresh service-role client. Sign-in calls replace the shared client's
    # session with the user JWT, and a lookup on that client fails closed.
    sb = await create_auth_client()

    # API key path (Wix flow): token is the raw api_key from users.api_key
    if not _looks_like_jwt(token):
        try:
            result = await sb.table("users").select("*").eq("api_key", token).limit(1).execute()
        except Exception as exc:
            logger.warning("API key lookup failed: %s", exc)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key") from exc
        rows = _rows(result)
        if not rows:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        return rows[0]

    # JWT path (Supabase): verify signature via JWKS (RS256/ES256) or legacy HS256 secret
    supabase_uid = _verify_supabase_jwt(token)

    try:
        result = await sb.table("users").select("*").eq("supabase_uid", supabase_uid).limit(1).execute()
    except Exception as exc:
        logger.warning("JWT user lookup failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found") from exc
    rows = _rows(result)
    if rows:
        return rows[0]

    # Google sign-in on the phone returns a Supabase token without calling /login,
    # so the users row may not exist yet.
    email = _jwt_email(token) or f"{supabase_uid[:8]}@users.local"
    try:
        inserted = (
            await sb.table("users")
            .insert({"supabase_uid": supabase_uid, "email": email, "tier": "free"})
            .execute()
        )
    except Exception as exc:
        logger.warning("JWT user create failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found") from exc
    created = _rows(inserted)
    if not created:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return created[0]


# Convenience type alias for router function signatures
CurrentUser = dict
