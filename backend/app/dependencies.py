"""FastAPI dependencies — JWT auth, current user."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings
from app.services.supabase_client import get_supabase


async def get_current_user(authorization: str = Header(...)) -> dict:
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        ) from exc

    supabase_uid: str | None = payload.get("sub")
    if not supabase_uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim")

    # Fetch internal user row
    sb = await get_supabase()
    result = await sb.table("users").select("*").eq("supabase_uid", supabase_uid).single().execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return result.data


# Convenience type alias for router function signatures
CurrentUser = dict
