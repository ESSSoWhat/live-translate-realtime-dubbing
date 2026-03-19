"""FastAPI dependencies — JWT or API key auth, current user."""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt  # type: ignore[import-untyped]

from app.config import get_settings
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def _looks_like_jwt(token: str) -> bool:
    """True if token has three dot-separated parts (JWT shape)."""
    return token.count(".") == 2 and len(token) > 20


async def get_current_user(authorization: str = Header(...)) -> dict:  # noqa: B008
    """
    Validate Bearer token: either Supabase JWT or backend API key.
    Returns the user row (id, email, tier, subscription_status, ...).
    Raises HTTP 401 if token is missing or invalid.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    sb = await get_supabase()

    # API key path (Wix flow): token is the raw api_key from users.api_key
    if not _looks_like_jwt(token):
        result = await sb.table("users").select("*").eq("api_key", token).maybe_single().execute()
        if result and result.data:
            return result.data
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # JWT path (Supabase / legacy)
    cfg = get_settings()
    try:
        payload = jwt.decode(
            token,
            cfg.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        logger.warning("JWT validation failed", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    supabase_uid: str | None = payload.get("sub")
    if not supabase_uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim")

    result = await sb.table("users").select("*").eq("supabase_uid", supabase_uid).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return result.data


# Convenience type alias for router function signatures
CurrentUser = dict
