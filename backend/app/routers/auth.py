"""Authentication endpoints — login, register, refresh, logout, OAuth, API key (Wix)."""

from __future__ import annotations

import contextlib
import secrets
import urllib.parse

import structlog  # pylint: disable=import-error
from fastapi import APIRouter, HTTPException, Query, Request, status  # pylint: disable=import-error
from postgrest.exceptions import APIError as PostgrestAPIError  # pylint: disable=import-error
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse, Response  # pylint: disable=import-error
from pydantic import BaseModel, Field  # pylint: disable=import-error

from app.config import get_settings
from app.models.requests import ApiKeyRequest, ForgotPasswordRequest, LoginRequest, RefreshRequest, RegisterRequest
from app.models.responses import AuthResponse, TokenResponse
from app.services.supabase_client import get_supabase
from app.services.usage import get_usage_snapshot

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_DESKTOP_SSO_HTML_PATH = Path(__file__).resolve().parent.parent / "templates" / "desktop_sso.html"


@router.get("/", include_in_schema=False)
async def auth_info() -> dict:
    """Auth module info; use POST /login, /register, /api-key, etc."""
    return {
        "auth": "ok",
        "endpoints": [
            "login",
            "register",
            "api-key",
            "desktop-handoff",
            "desktop-sso",
            "refresh",
            "oauth/google",
            "oauth/apple",
        ],
    }

def _default_usage(tier: str = "free") -> dict:
    """Return default usage snapshot when DB is unavailable."""
    from datetime import date, timedelta
    today = date.today()
    if today.month == 12:
        period_end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        period_end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    # Must match tier_limits in supabase_schema.sql / WIX_SYNC.md:
    # free 30min, starter (Hobby) 5hr, pro 15hr, early_adopters unlimited.
    _UNLIMITED = 2147483647
    limits = {
        "free": (1800, 50000, 1800, 50000, 1),
        "starter": (18000, 500000, 18000, 500000, 5),
        "pro": (54000, 2000000, 54000, 2000000, 20),
        "early_adopters": (_UNLIMITED, _UNLIMITED, _UNLIMITED, _UNLIMITED, 99),
    }
    dub, tts, stt, trans, clones = limits.get(tier, limits["free"])
    return {
        "dubbing_seconds_used": 0, "dubbing_seconds_limit": dub,
        "tts_chars_used": 0, "tts_chars_limit": tts,
        "stt_seconds_used": 0, "stt_seconds_limit": stt,
        "translation_chars_used": 0, "translation_chars_limit": trans,
        "voice_clones_used": 0, "voice_clones_limit": clones,
        "period_reset_date": str(period_end),
    }


def _verify_wix_secret(request: Request) -> None:
    """Verify Wix request via X-Wix-Sync-Secret or Bearer. Raises HTTPException on failure."""
    cfg = get_settings()
    secret = (cfg.lt_sync_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wix API key not configured. Set LT_SYNC_SECRET.",
        )
    auth = request.headers.get("Authorization") or request.headers.get("X-Wix-Sync-Secret")
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Wix auth")
    token = auth.replace("Bearer ", "").strip() if auth.startswith("Bearer ") else auth.strip()
    if token != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Wix secret")


@router.post("/api-key", status_code=status.HTTP_200_OK)
async def create_or_get_api_key(body: ApiKeyRequest, request: Request) -> dict:
    """
    Create or return an API key for the given email (Wix-only).
    Call from Wix Velo after member login; show the returned api_key once on the account page.
    """
    _verify_wix_secret(request)
    sb = await get_supabase()

    # Query for existing user
    existing_data = None
    try:
        existing = await sb.table("users").select("id", "email", "tier", "api_key").eq("email", body.email).limit(1).execute()
        if existing is not None and existing.data and len(existing.data) > 0:
            existing_data = existing.data[0]
    except PostgrestAPIError as e:
        if e.code != "204" and "Missing response" not in str(e):
            logger.error("API key: database query failed", email=body.email, error=str(e))
            raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        # Handle case where execute() returns None or other unexpected errors
        logger.warning("API key: query returned unexpected result", email=body.email, error=str(e))

    if existing_data and existing_data.get("api_key"):
        return {
            "api_key": existing_data["api_key"],
            "user_id": str(existing_data["id"]),
            "email": existing_data["email"],
            "tier": existing_data.get("tier", "free"),
        }
    api_key = secrets.token_urlsafe(32)
    if existing_data:
        await sb.table("users").update({"api_key": api_key}).eq("id", existing_data["id"]).execute()
        user_id = existing_data["id"]
        tier = existing_data.get("tier", "free")
    else:
        insert_result = (
            await sb.table("users")
            .insert({"email": body.email, "tier": "free", "subscription_status": "active", "api_key": api_key})
            .execute()
        )
        if not insert_result.data or (isinstance(insert_result.data, list) and len(insert_result.data) == 0):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create user")
        row = insert_result.data[0] if isinstance(insert_result.data, list) else insert_result.data
        user_id = row["id"]
        tier = row.get("tier", "free")
    logger.info("API key provisioned", email=body.email, user_id=str(user_id))
    return {"api_key": api_key, "user_id": str(user_id), "email": body.email, "tier": tier}


class DesktopHandoffRequest(BaseModel):
    """Wix → backend: deliver API key for a desktop waiting session."""

    session_id: str
    api_key: str


@router.post("/desktop-handoff", status_code=status.HTTP_200_OK)
async def store_desktop_handoff(body: DesktopHandoffRequest, request: Request) -> dict:
    """
    Store an API key for a desktop SSO session.

    Called from Wix after member login. The desktop app polls
    ``GET /auth/desktop-handoff/{session_id}`` until the key appears.
    This avoids relying on Wix navigating to ``http://localhost``.
    """
    _verify_wix_secret(request)
    session_id = (body.session_id or "").strip()
    api_key = (body.api_key or "").strip()
    if not session_id or len(session_id) < 16:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid session_id")
    if not api_key or len(api_key) < 16:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid api_key")

    from app.services.desktop_handoff import put_handoff

    await put_handoff(session_id, api_key)
    logger.info("Desktop handoff stored", session_prefix=session_id[:8])
    return {"ok": True}


@router.get("/desktop-handoff/{session_id}", status_code=status.HTTP_200_OK)
async def poll_desktop_handoff(session_id: str) -> dict:
    """
    Poll for a completed Wix → desktop handoff.

    Returns ``{"ready": false}`` until the key is available, then
    ``{"ready": true, "api_key": "..."}`` once (token is consumed).
    """
    sid = (session_id or "").strip()
    if not sid or len(sid) < 16:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid session_id")

    from app.services.desktop_handoff import take_handoff

    api_key = await take_handoff(sid)
    if not api_key:
        return {"ready": False}
    return {"ready": True, "api_key": api_key}


def _is_valid_desktop_redirect(uri: str) -> bool:
    """Allow localhost callbacks and Live Translate https hosts only."""
    try:
        parsed = urllib.parse.urlparse(uri)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            return parsed.scheme == "http"
        if parsed.scheme != "https":
            return False
        trusted = (
            "livetranslate.app",
            "www.livetranslate.app",
            "livetranslate.net",
            "www.livetranslate.net",
        )
        return host in trusted or any(host.endswith("." + t) for t in trusted)
    except Exception:
        return False


async def _ensure_user_api_key(sb, user_row: dict) -> str:
    """Return existing users.api_key or provision one."""
    existing = (user_row.get("api_key") or "").strip()
    if existing:
        return existing
    api_key = secrets.token_urlsafe(32)
    await sb.table("users").update({"api_key": api_key}).eq("id", user_row["id"]).execute()
    return api_key


class DesktopSsoCompleteRequest(BaseModel):
    """Complete desktop browser sign-in without relying on Wix page code."""

    session_id: str = Field(min_length=16)
    redirect_uri: str | None = None
    email: str | None = None
    password: str | None = None
    api_key: str | None = None
    access_token: str | None = None
    oauth_code: str | None = None
    code_verifier: str | None = None


@router.get("/desktop-sso", response_class=HTMLResponse, include_in_schema=False)
async def desktop_sso_page() -> HTMLResponse:
    """
    Browser sign-in bridge for the desktop app.

    Wix ``/app-auth`` on production is a static marketing page (Git publish targets a
    different site), so the app opens this backend page instead. On success the page
    redirects to ``redirect_uri?api_key=...`` and stores a handoff for polling.
    """
    if not _DESKTOP_SSO_HTML_PATH.is_file():
        raise HTTPException(status_code=500, detail="desktop_sso template missing")
    return HTMLResponse(_DESKTOP_SSO_HTML_PATH.read_text(encoding="utf-8"))


@router.post("/desktop-sso/complete", status_code=status.HTTP_200_OK)
async def desktop_sso_complete(body: DesktopSsoCompleteRequest) -> dict:
    """Validate credentials / API key / Google JWT, store handoff, return redirect URL."""
    from app.dependencies import _verify_supabase_jwt
    from app.services.desktop_handoff import put_handoff

    session_id = body.session_id.strip()
    redirect_uri = (body.redirect_uri or "").strip()
    if redirect_uri and not _is_valid_desktop_redirect(redirect_uri):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid redirect_uri")

    sb = await get_supabase()
    api_key: str | None = None

    pasted = (body.api_key or "").strip()
    access_token = (body.access_token or "").strip()
    oauth_code = (body.oauth_code or "").strip()
    if oauth_code and not access_token:
        try:
            exchange_params: dict = {"auth_code": oauth_code}
            if body.code_verifier:
                exchange_params["code_verifier"] = body.code_verifier.strip()
            exchanged = await sb.auth.exchange_code_for_session(exchange_params)
        except Exception as exc:
            logger.error("Desktop SSO OAuth code exchange failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google sign-in expired. Close this tab and try again from the app.",
            ) from exc
        if exchanged is None or exchanged.session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google sign-in did not return a session",
            )
        access_token = exchanged.session.access_token
    if pasted:
        result = await sb.table("users").select("*").eq("api_key", pasted).maybe_single().execute()
        if not result or not result.data:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        api_key = pasted
    elif access_token:
        try:
            supabase_uid = _verify_supabase_jwt(access_token)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google session",
            ) from exc
        result = (
            await sb.table("users")
            .select("*")
            .eq("supabase_uid", supabase_uid)
            .maybe_single()
            .execute()
        )
        if not result or not result.data:
            # Resolve email from JWT user if needed — create row like OAuth exchange
            try:
                user_resp = await sb.auth.get_user(access_token)
                email = (user_resp.user.email if user_resp and user_resp.user else None) or ""
            except Exception:
                email = ""
            insert = (
                await sb.table("users")
                .insert(
                    {
                        "supabase_uid": supabase_uid,
                        "email": email or f"{supabase_uid[:8]}@users.local",
                        "tier": "free",
                        "api_key": secrets.token_urlsafe(32),
                    }
                )
                .execute()
            )
            if not insert.data:
                raise HTTPException(status_code=500, detail="Could not create user")
            user_row = insert.data[0] if isinstance(insert.data, list) else insert.data
        else:
            user_row = result.data
        api_key = await _ensure_user_api_key(sb, user_row)
    elif body.email and body.password:
        try:
            resp = await sb.auth.sign_in_with_password(
                {"email": body.email.strip(), "password": body.password}
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            ) from exc
        if resp.user is None or resp.session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login failed")
        result = (
            await sb.table("users")
            .select("*")
            .eq("supabase_uid", resp.user.id)
            .maybe_single()
            .execute()
        )
        if not result or not result.data:
            insert = (
                await sb.table("users")
                .insert(
                    {
                        "supabase_uid": resp.user.id,
                        "email": body.email.strip(),
                        "tier": "free",
                        "api_key": secrets.token_urlsafe(32),
                    }
                )
                .execute()
            )
            if not insert.data:
                raise HTTPException(status_code=500, detail="Could not create user")
            user_row = insert.data[0] if isinstance(insert.data, list) else insert.data
        else:
            user_row = result.data
        api_key = await _ensure_user_api_key(sb, user_row)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide email/password, api_key, access_token, or oauth_code",
        )

    # Sync website Pricing Plan → users.tier so desktop usage matches Wix package.
    try:
        from app.services.wix_plans import refresh_user_tier_from_wix

        if pasted:
            user_for_refresh = result.data  # type: ignore[name-defined]
        else:
            user_for_refresh = user_row  # type: ignore[name-defined]
        if isinstance(user_for_refresh, dict):
            await refresh_user_tier_from_wix(user_for_refresh, force=True)
    except Exception as exc:
        logger.warning("Desktop SSO Wix tier refresh skipped", error=str(exc))

    await put_handoff(session_id, api_key)
    logger.info("Desktop SSO complete", session_prefix=session_id[:8])

    out: dict = {"ok": True, "api_key": api_key}
    if redirect_uri:
        sep = "&" if "?" in redirect_uri else "?"
        out["redirect"] = f"{redirect_uri}{sep}api_key={urllib.parse.quote(api_key, safe='')}"
    return out


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
        if not insert_result.data or (
            isinstance(insert_result.data, list) and len(insert_result.data) == 0
        ):
            raise ValueError("Insert returned no data")
        user_row = (
            insert_result.data
            if isinstance(insert_result.data, dict)
            else insert_result.data[0]
        )
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

    try:
        usage = await get_usage_snapshot(str(user_row["id"]))
    except Exception as exc:
        logger.warning("Failed to fetch usage, using defaults", error=str(exc))
        usage = _default_usage("free")

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
    if not result or not result.data:
        # Auto-create if missing (e.g. user registered via web)
        try:
            result = (
                await sb.table("users")
                .insert({"supabase_uid": resp.user.id, "email": body.email, "tier": "free"})
                .execute()
            )
            if not result.data or (isinstance(result.data, list) and len(result.data) == 0):
                raise ValueError("Insert returned no data")
            user_row = result.data if isinstance(result.data, dict) else result.data[0]
        except Exception as exc:
            logger.exception("Failed to create or read internal user row after login")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Login could not be completed",
            ) from exc
    else:
        user_row = result.data if isinstance(result.data, dict) else result.data[0]

    try:
        usage = await get_usage_snapshot(str(user_row["id"]))
    except Exception as exc:
        logger.warning("Failed to fetch usage, using defaults", error=str(exc))
        usage = _default_usage(user_row["tier"])

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
_OAUTH_CODE_CHALLENGE_QUERY = Query(
    None,
    description="PKCE S256 challenge. Required for the HTTPS desktop-sso bridge.",
)


@router.get("/oauth/google")
async def google_oauth_url(
    redirect_uri: str = _OAUTH_REDIRECT_URI_QUERY,
    code_challenge: str | None = _OAUTH_CODE_CHALLENGE_QUERY,
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
    params: dict[str, str] = {
        "provider": "google",
        "redirect_to": redirect_uri,
    }
    challenge = (code_challenge or "").strip()
    if challenge:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    else:
        # Native localhost callbacks still advertise PKCE; the desktop client
        # injects code_challenge itself. Do not set flow_type=pkce for the
        # HTTPS bridge — without a challenge the user lands on ?code= and
        # the page cannot finish.
        parsed = urllib.parse.urlparse(redirect_uri)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            params["flow_type"] = "pkce"
    url = f"{supabase_base}/auth/v1/authorize?{urllib.parse.urlencode(params)}"
    logger.info(
        "Generated Google OAuth URL",
        redirect_uri=redirect_uri,
        pkce=bool(challenge) or "flow_type" in params,
    )
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
    if not result or not result.data:
        try:
            result = (
                await sb.table("users")
                .insert({
                    "supabase_uid": resp.user.id,
                    "email": resp.user.email or "",
                    "tier": "free",
                })
                .execute()
            )
            if not result.data or (isinstance(result.data, list) and len(result.data) == 0):
                raise ValueError("Insert returned no data")
            user_row = result.data if isinstance(result.data, dict) else result.data[0]
        except Exception as exc:
            logger.exception("Failed to create or read internal user row after OAuth exchange")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OAuth sign-in could not be completed",
            ) from exc
    else:
        user_row = result.data if isinstance(result.data, dict) else result.data[0]

    try:
        usage = await get_usage_snapshot(str(user_row["id"]))
    except Exception as exc:
        logger.warning("Failed to fetch usage, using defaults", error=str(exc))
        usage = _default_usage(user_row["tier"])

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
    if not result or not result.data:
        try:
            result = (
                await sb.table("users")
                .insert({
                    "supabase_uid": resp.user.id,
                    "email": resp.user.email or "",
                    "tier": "free",
                })
                .execute()
            )
            if not result.data or (isinstance(result.data, list) and len(result.data) == 0):
                raise ValueError("Insert returned no data")
            user_row = result.data if isinstance(result.data, dict) else result.data[0]
        except Exception as exc:
            logger.exception("Failed to create or read internal user row after ID token sign-in")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{provider.title()} sign-in could not be completed",
            ) from exc
    else:
        user_row = result.data if isinstance(result.data, dict) else result.data[0]

    try:
        usage = await get_usage_snapshot(str(user_row["id"]))
    except Exception as exc:
        logger.warning("Failed to fetch usage, using defaults", error=str(exc))
        usage = _default_usage(user_row["tier"])

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
