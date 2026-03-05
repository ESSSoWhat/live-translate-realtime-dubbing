"""Authentication endpoints — login, register, refresh, logout, OAuth."""

from __future__ import annotations

import contextlib
import urllib.parse

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.config import get_settings
from app.models.requests import ForgotPasswordRequest, LoginRequest, RefreshRequest, RegisterRequest
from app.models.responses import AuthResponse, TokenResponse
from app.services.supabase_client import get_supabase
from app.services.usage import get_usage_snapshot

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> AuthResponse:
    """Create a new account."""
    sb = await get_supabase()

    try:
        resp = await sb.auth.sign_up({"email": body.email, "password": body.password})
    except Exception as exc:
        logger.exception("Registration failed")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed") from exc

    if resp.user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration failed")

    # If email confirmation is required, session may be None
    session = resp.session
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please check your email to confirm your account, then sign in.",
        )

    # Create internal user row (rollback Supabase user on insert failure)
    try:
        insert_result = (
            await sb.table("users")
            .insert({"supabase_uid": resp.user.id, "email": body.email, "tier": "free"})
            .execute()
        )
        user_row = insert_result.data[0]
    except Exception as exc:
        logger.exception("Failed to create internal user row")
        try:
            await sb.auth.admin.delete_user(resp.user.id)
        except Exception as cleanup_exc:
            logger.warning("Could not delete Supabase user after insert failure", error=str(cleanup_exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration could not be completed",
        ) from exc

    usage = await get_usage_snapshot(str(user_row["id"]))

    return AuthResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in or 3600,
        user_id=str(user_row["id"]),
        email=body.email,
        tier="free",
        usage=usage,  # type: ignore[arg-type]
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    """Login with email and password."""
    sb = await get_supabase()

    try:
        resp = await sb.auth.sign_in_with_password({"email": body.email, "password": body.password})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from exc

    if resp.user is None or resp.session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login failed")

    # Fetch internal user row (maybe_single so auto-create path runs when no row)
    result = await sb.table("users").select("*").eq("supabase_uid", resp.user.id).maybe_single().execute()
    if not result.data:
        # Auto-create if missing (e.g. user registered via web)
        result = (
            await sb.table("users")
            .insert({"supabase_uid": resp.user.id, "email": body.email, "tier": "free"})
            .execute()
        )
    user_row = result.data if isinstance(result.data, dict) else result.data[0]

    usage = await get_usage_snapshot(str(user_row["id"]))

    return AuthResponse(
        access_token=resp.session.access_token,
        refresh_token=resp.session.refresh_token,
        expires_in=resp.session.expires_in or 3600,
        user_id=str(user_row["id"]),
        email=body.email,
        tier=user_row["tier"],
        usage=usage,  # type: ignore[arg-type]
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    """Exchange a refresh token for a new access token."""
    sb = await get_supabase()

    try:
        resp = await sb.auth.refresh_session(body.refresh_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc

    if resp.session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh failed")

    return TokenResponse(
        access_token=resp.session.access_token,
        refresh_token=resp.session.refresh_token,
        expires_in=resp.session.expires_in or 3600,
    )


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest) -> Response:
    """Trigger a password reset email via Supabase."""
    sb = await get_supabase()
    with contextlib.suppress(Exception):
        await sb.auth.reset_password_email(body.email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Google OAuth (desktop / native client flow) ──────────────────────────────

# B008: Query() is used by FastAPI for injection; module-level avoids "call in default" lint.
_OAUTH_REDIRECT_URI_QUERY = Query(
    ...,
    description=(
        "Desktop app callback URL — e.g. http://localhost:PORT/. "
        "Must be in Supabase → Auth → URL Configuration → Additional Redirect URLs."
    ),
)


@router.get("/oauth/google")
async def google_oauth_url(
    redirect_uri: str = _OAUTH_REDIRECT_URI_QUERY,
) -> JSONResponse:
    """
    Return the Google OAuth redirect URL for the desktop client.

    The caller opens this URL in the system browser.  After the user
    authenticates with Google, Supabase redirects to ``redirect_uri``
    with the session tokens in the URL fragment (implicit flow) or a
    one-time ``code`` in the query string (PKCE flow).

    Required Supabase setup:
    * Google provider enabled in Auth → Providers.
    * ``redirect_uri`` (or ``http://localhost/**``) added to
      Auth → URL Configuration → Additional Redirect URLs.
    """
    cfg = get_settings()
    supabase_base = cfg.supabase_url.rstrip("/")
    params = urllib.parse.urlencode({
        "provider": "google",
        "redirect_to": redirect_uri,
    })
    url = f"{supabase_base}/auth/v1/authorize?{params}"
    logger.info("Generated Google OAuth URL", redirect_uri=redirect_uri)
    return JSONResponse({"url": url})


class _OAuthCodeExchangeRequest(BaseModel):
    code: str
    redirect_uri: str  # Must match the redirect_uri used when starting OAuth
    code_verifier: str | None = None  # PKCE code_verifier (required if PKCE was used)


@router.post("/oauth/google/exchange", response_model=AuthResponse)
async def google_oauth_exchange(body: _OAuthCodeExchangeRequest) -> AuthResponse:
    """
    Exchange a Supabase PKCE ``code`` for a full session.

    Call this after the desktop app receives ``?code=...`` at the callback
    URL.  Returns the same ``AuthResponse`` as the password-login endpoint
    so the caller can store and use the tokens identically.
    """
    sb = await get_supabase()

    try:
        exchange_params: dict = {"auth_code": body.code}
        if body.code_verifier:
            exchange_params["code_verifier"] = body.code_verifier
        resp = await sb.auth.exchange_code_for_session(exchange_params)
    except Exception as exc:
        logger.error("OAuth code exchange failed", error=str(exc), code_len=len(body.code))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OAuth code exchange failed",
        ) from exc

    if resp.user is None or resp.session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OAuth exchange returned no session",
        )

    # Upsert internal user row (maybe_single so creation path runs when missing)
    result = (
        await sb.table("users")
        .select("*")
        .eq("supabase_uid", resp.user.id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        result = (
            await sb.table("users")
            .insert({
                "supabase_uid": resp.user.id,
                "email": resp.user.email or "",
                "tier": "free",
            })
            .execute()
        )
    user_row = result.data if isinstance(result.data, dict) else result.data[0]

    usage = await get_usage_snapshot(str(user_row["id"]))

    logger.info("Google OAuth exchange complete", user_id=resp.user.id)
    return AuthResponse(
        access_token=resp.session.access_token,
        refresh_token=resp.session.refresh_token,
        expires_in=resp.session.expires_in or 3600,
        user_id=str(user_row["id"]),
        email=resp.user.email or "",
        tier=user_row["tier"],
        usage=usage,  # type: ignore[arg-type]
    )


# ── ID Token login (native mobile SDKs) ───────────────────────────────────────


class _IdTokenRequest(BaseModel):
    id_token: str
    nonce: str | None = None


async def _id_token_login(provider: str, id_token: str, nonce: str | None) -> AuthResponse:
    """Common logic for ID token sign-in (Google/Apple native SDKs)."""
    sb = await get_supabase()

    try:
        sign_in_params: dict = {"provider": provider, "token": id_token}
        if nonce:
            sign_in_params["nonce"] = nonce
        resp = await sb.auth.sign_in_with_id_token(sign_in_params)
    except Exception as exc:
        logger.error(f"{provider} ID token sign-in failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{provider.title()} sign-in failed",
        ) from exc

    if resp.user is None or resp.session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{provider.title()} sign-in returned no session",
        )

    # Upsert internal user row
    result = (
        await sb.table("users")
        .select("*")
        .eq("supabase_uid", resp.user.id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        result = (
            await sb.table("users")
            .insert({
                "supabase_uid": resp.user.id,
                "email": resp.user.email or "",
                "tier": "free",
            })
            .execute()
        )
    user_row = result.data if isinstance(result.data, dict) else result.data[0]

    usage = await get_usage_snapshot(str(user_row["id"]))

    logger.info(f"{provider} ID token sign-in complete", user_id=resp.user.id)
    return AuthResponse(
        access_token=resp.session.access_token,
        refresh_token=resp.session.refresh_token,
        expires_in=resp.session.expires_in or 3600,
        user_id=str(user_row["id"]),
        email=resp.user.email or "",
        tier=user_row["tier"],
        usage=usage,  # type: ignore[arg-type]
    )


@router.post("/oauth/google/id-token", response_model=AuthResponse)
async def google_id_token_login(body: _IdTokenRequest) -> AuthResponse:
    """Login with Google ID token from native Google Sign-In SDK."""
    return await _id_token_login("google", body.id_token, body.nonce)


@router.post("/oauth/apple/id-token", response_model=AuthResponse)
async def apple_id_token_login(body: _IdTokenRequest) -> AuthResponse:
    """Login with Apple ID token from native Sign in with Apple."""
    return await _id_token_login("apple", body.id_token, body.nonce)


# ── Apple OAuth (web flow) ────────────────────────────────────────────────────


@router.get("/oauth/apple")
async def apple_oauth_url(
    redirect_uri: str = _OAUTH_REDIRECT_URI_QUERY,
) -> JSONResponse:
    """Return the Apple OAuth redirect URL for web/desktop clients."""
    cfg = get_settings()
    supabase_base = cfg.supabase_url.rstrip("/")
    params = urllib.parse.urlencode({
        "provider": "apple",
        "redirect_to": redirect_uri,
    })
    url = f"{supabase_base}/auth/v1/authorize?{params}"
    logger.info("Generated Apple OAuth URL", redirect_uri=redirect_uri)
    return JSONResponse({"url": url})
