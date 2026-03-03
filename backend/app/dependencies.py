"""FastAPI dependencies — JWT auth, current user."""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt  # type: ignore[import-untyped]

from app.config import get_settings
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)


async def get_current_user(authorization: str = Header(...)) -> dict:  # noqa: B008
    """
    Validate the Bearer JWT and return the user row from Supabase.

    Raises HTTP 401 if token is missing, invalid, or expired.
    """
    cfg = get_settings()

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

    token = authorization[len("Bearer "):]

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

    # Fetch internal user row (maybe_single avoids APIError when no/multiple rows)
    sb = await get_supabase()
    result = await sb.table("users").select("*").eq("supabase_uid", supabase_uid).maybe_single().execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return result.data


# Convenience type alias for router function signatures
CurrentUser = dict
