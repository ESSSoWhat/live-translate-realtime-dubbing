"""
Login dialog — shown at startup if no valid auth token is found.

Supports:
  - Email / password login and registration.
  - Google OAuth (opens Chrome, captures redirect via a local HTTP server).

On QDialog.Accepted:
    self.auth_response  — full dict from the backend (access_token, refresh_token,
                          tier, usage, user_id, email).
"""
# Pylint: widgets are assigned in _build_ui; docstrings on thread run() are minimal.
# pylint: disable=attribute-defined-outside-init,missing-function-docstring

from __future__ import annotations

import base64
import contextlib
import hashlib
import json as _json
import secrets
import webbrowser
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from live_dubbing.config.settings import AppSettings

logger = structlog.get_logger(__name__)


def _free_tier_defaults() -> dict:
    """Default usage/limits for free tier — used when backend is unreachable.

    Must match backend tier_limits (supabase_schema.sql) for free tier:
    dubbing_seconds=1800, stt_seconds=1800, tts_chars=50000, voice_clones=1.
    """
    import datetime
    today = datetime.date.today()
    # Last day of current month
    if today.month == 12:
        period_end = datetime.date(today.year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        period_end = datetime.date(today.year, today.month + 1, 1) - datetime.timedelta(days=1)
    return {
        "tier": "free",
        "dubbing_seconds_used": 0,
        "dubbing_seconds_limit": 1800,
        "tts_chars_used": 0,
        "tts_chars_limit": 50000,
        "stt_seconds_used": 0,
        "stt_seconds_limit": 1800,
        "voice_clones_used": 0,
        "voice_clones_limit": 1,
        "period_reset_date": str(period_end),
    }


# ── Email / password worker ───────────────────────────────────────────────────

class _AuthWorker(QThread):
    """Background thread for email/password auth API calls (keeps Qt responsive)."""

    success = pyqtSignal(dict)   # emits full auth response dict
    error = pyqtSignal(str)      # emits error message string

    def __init__(self, base_url: str, mode: str, email: str, password: str) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._mode = mode  # "login" or "register"
        self._email = email
        self._password = password

    def run(self) -> None:
        """Run auth request (direct Supabase or backend)."""  # noqa: D400
        import os as _os
        _sb_url = _os.environ.get("LIVE_TRANSLATE_SUPABASE_URL", "").rstrip("/")
        _sb_anon = _os.environ.get("LIVE_TRANSLATE_SUPABASE_ANON_KEY", "")
        if _sb_url and _sb_anon:
            self._run_direct(_sb_url, _sb_anon)
        else:
            self._run_backend()

    def _run_direct(self, supabase_url: str, anon_key: str) -> None:
        """Call Supabase Auth REST API directly; no backend required."""
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._mode == "login":
                resp = httpx.post(
                    f"{supabase_url}/auth/v1/token?grant_type=password",
                    json={"email": self._email, "password": self._password},
                    headers=headers,
                    timeout=15.0,
                    follow_redirects=True,
                )
            else:  # register
                resp = httpx.post(
                    f"{supabase_url}/auth/v1/signup",
                    json={"email": self._email, "password": self._password},
                    headers=headers,
                    timeout=15.0,
                    follow_redirects=True,
                )

            data = resp.json() if resp.content else {}

            if resp.status_code in (200, 201):
                access_token = data.get("access_token", "")
                refresh_token = data.get("refresh_token", "")

                if not access_token:
                    # Registration returned 200 but no token — email confirmation needed
                    self.error.emit(
                        "Check your email for a confirmation link, then sign in."
                    )
                    return

                # Decode user_id / email from JWT payload
                user_id, email = "", self._email
                try:
                    payload_b64 = access_token.split(".")[1]
                    payload_b64 += "=" * (-len(payload_b64) % 4)
                    payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
                    user_id = payload.get("sub", "")
                    email = payload.get("email", self._email)
                except Exception:
                    pass

                self.success.emit({
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "tier": "free",
                    "usage": _free_tier_defaults(),
                    "user_id": user_id,
                    "email": email,
                })
            else:
                msg = (
                    data.get("error_description")
                    or data.get("msg")
                    or data.get("message")
                    or data.get("error")
                    or f"HTTP {resp.status_code}"
                )
                self.error.emit(str(msg))
        except httpx.ConnectError:
            self.error.emit("Cannot connect to Supabase. Check your internet connection.")
        except httpx.TimeoutException:
            self.error.emit("Connection timed out. Please try again.")
        except Exception as exc:
            self.error.emit(str(exc))

    def _run_backend(self) -> None:
        """Call the Live Translate backend (legacy path)."""
        try:
            endpoint = f"{self._base_url}/api/v1/auth/{self._mode}"
            with httpx.Client(timeout=15.0) as client:
                response = client.post(
                    endpoint,
                    json={"email": self._email, "password": self._password},
                )
            if response.status_code in (200, 201):
                self.success.emit(response.json())
            else:
                try:
                    body = response.json()
                    detail = body.get("detail", "Unknown error")
                    if isinstance(detail, list):
                        parts = []
                        for item in detail:
                            if isinstance(item, dict) and "msg" in item:
                                parts.append(str(item["msg"]))
                            else:
                                parts.append(str(item))
                        detail = " ".join(parts) if parts else "Invalid request"
                    else:
                        detail = str(detail)
                except Exception:
                    detail = f"HTTP {response.status_code}"
                self.error.emit(detail)
        except httpx.ConnectError:
            self.error.emit("Cannot connect to server. Check your internet connection.")
        except httpx.TimeoutException:
            self.error.emit("Connection timed out. Please try again.")
        except Exception as exc:
            self.error.emit(str(exc))


# ── Google OAuth worker ───────────────────────────────────────────────────────

def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256).

    Returns:
        (code_verifier, code_challenge) tuple
    """
    # code_verifier: 43-128 URL-safe characters
    code_verifier = secrets.token_urlsafe(64)[:96]
    # code_challenge: base64url(sha256(code_verifier))
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


class _OAuthWorker(QThread):
    """
    Drive the full Google OAuth flow in a background thread.

    1. Starts a local HTTP callback server on a free port.
    2. Fetches the Google OAuth URL from the backend
       (``GET /api/v1/auth/oauth/google?redirect_uri=...``).
    3. Opens the URL in the system browser (Chrome / default browser).
    4. Blocks until the local server captures the tokens (or times out).
    5. Fetches the user profile (tier/usage) using the received token.
    6. Emits ``success`` with a fully-populated auth dict.
    """

    success = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        # PKCE: generate verifier/challenge pair for secure code exchange
        self._code_verifier, self._code_challenge = _generate_pkce_pair()

    def run(self) -> None:  # noqa: D402
        """Entry point for the OAuth worker thread.

        Runs _run() and handles exceptions.
        """
        try:
            self._run()
        except Exception as exc:
            # Top-level safety net — QThread swallows unhandled exceptions silently
            logger.exception("Unhandled error in _OAuthWorker", error=str(exc))
            self.error.emit(f"Unexpected error during Google sign-in: {exc}")

    def _run(self) -> None:
        """Actual OAuth flow (called from run() under a try/except guard)."""
        from live_dubbing.services.oauth_callback_server import OAuthCallbackServer

        server = OAuthCallbackServer()
        port = server.start()
        redirect_uri = server.redirect_uri   # http://localhost:{port}/
        logger.info("OAuth callback server started", port=port)

        # ── 1. Get OAuth URL from backend ────────────────────────────────
        # Strategy:
        #   a) If LIVE_TRANSLATE_SUPABASE_URL env var is set, construct the
        #      Supabase authorize URL directly — no backend call needed.
        #   b) Otherwise call the backend endpoint and follow its redirect chain,
        #      stopping *only* at known Supabase / Google OAuth domains.
        #      This prevents treating a CDN / marketing-site redirect (e.g.
        #      api.example.com → marketing.example.com) as the OAuth URL.
        import os as _os
        import urllib.parse as _urlparse  # local import to avoid polluting module scope

        # Domains that signal "this IS the Supabase/Google OAuth URL"
        _OAUTH_DOMAINS = ("supabase.co", "supabase.in", "accounts.google.com")
        oauth_url: str | None = None

        # ── 1a. Direct Supabase URL shortcut (bypasses backend) ──────────
        _direct_supabase = _os.environ.get("LIVE_TRANSLATE_SUPABASE_URL", "").rstrip("/")
        if _direct_supabase:
            _supabase_params = _urlparse.urlencode({
                "provider": "google",
                "redirect_to": redirect_uri,
                "code_challenge": self._code_challenge,
                "code_challenge_method": "S256",
            })
            oauth_url = f"{_direct_supabase}/auth/v1/authorize?{_supabase_params}"
            logger.info("Constructed OAuth URL from LIVE_TRANSLATE_SUPABASE_URL env var")

        if oauth_url is None:
            # ── 1b. Fetch via backend, following redirects intelligently ──
            _MAX_HOPS = 15
            _current_url = f"{self._base_url}/api/v1/auth/oauth/google"
            _current_params: dict | None = {"redirect_uri": redirect_uri}

            try:
                with httpx.Client(timeout=10.0) as client:
                    for _hop in range(_MAX_HOPS):
                        resp = client.get(
                            _current_url,
                            params=_current_params,
                            follow_redirects=False,
                        )
                        _current_params = None  # only pass query params on the first hop

                        if resp.is_redirect:
                            _location = resp.headers.get("location", "")
                            if not _location:
                                server.stop()
                                self.error.emit(
                                    "Backend returned a redirect with no Location header."
                                )
                                return

                            logger.info(
                                "OAuth redirect hop",
                                hop=_hop,
                                status=resp.status_code,
                                location_preview=_location[:150],
                            )

                            _loc_host = _urlparse.urlparse(_location).netloc.lower()
                            if any(
                                _loc_host == d or _loc_host.endswith("." + d)
                                for d in _OAUTH_DOMAINS
                            ):
                                # Redirect goes directly to Supabase/Google — this IS
                                # the OAuth URL.  Open it in the browser.
                                oauth_url = _location
                                logger.info(
                                    "Got OAuth URL via Supabase/Google redirect",
                                    url_preview=_location[:150],
                                )
                                break
                            else:
                                # Non-OAuth external redirect (same-origin HTTP→HTTPS,
                                # CDN hop, etc.) — keep following.
                                _current_url = _location
                                continue

                        elif resp.status_code == 200:
                            _content_type = resp.headers.get("content-type", "")
                            if "json" in _content_type:
                                try:
                                    oauth_url = resp.json()["url"]
                                    logger.info("Got OAuth URL from backend JSON response")
                                except Exception:
                                    server.stop()
                                    self.error.emit(
                                        f"Backend returned unexpected JSON: {resp.text[:200]}"
                                    )
                                    return
                            else:
                                # HTML response — the backend API is unreachable and a
                                # CDN / marketing site is answering instead.
                                logger.warning(
                                    "OAuth endpoint returned HTML — backend unreachable?",
                                    url=_current_url[:100],
                                    content_type=_content_type,
                                )
                                server.stop()
                                self.error.emit(
                                    "Cannot reach the Live Translate server.\n"
                                    "Google sign-in is temporarily unavailable.\n"
                                    "Please use email/password login."
                                )
                                return
                            break

                        elif resp.status_code == 404:
                            server.stop()
                            self.error.emit(
                                "Google sign-in is not enabled on the server.\n"
                                "Please use email/password login for now."
                            )
                            return

                        else:
                            server.stop()
                            self.error.emit(
                                f"Server error ({resp.status_code}) during Google sign-in."
                            )
                            return

            except httpx.ConnectError as exc:
                server.stop()
                logger.error(
                    "Cannot connect to backend for OAuth URL",
                    url=self._base_url,
                    error=str(exc),
                )
                self.error.emit(
                    "Cannot connect to the Live Translate server.\n"
                    "Check your internet connection and try again.\n\n"
                    "For local development, set environment variables:\n"
                    "  LIVE_TRANSLATE_SUPABASE_URL\n"
                    "  LIVE_TRANSLATE_SUPABASE_ANON_KEY"
                )
                return
            except httpx.HTTPStatusError as exc:
                server.stop()
                logger.error(
                    "Backend OAuth URL request failed",
                    status=exc.response.status_code,
                )
                self.error.emit(
                    f"Server error ({exc.response.status_code}) starting Google sign-in."
                )
                return
            except Exception as exc:
                server.stop()
                logger.exception("Unexpected error getting OAuth URL", error=str(exc))
                self.error.emit(f"Could not start Google sign-in: {exc}")
                return

        if oauth_url is None:
            server.stop()
            self.error.emit(
                "Could not obtain OAuth URL from backend (too many redirects)."
            )
            return

        # ── 1c. Ensure OAuth URL has correct redirect_to and PKCE params ──
        # The backend may have hard-coded its own callback URL as redirect_to.
        # We also need to inject our PKCE code_challenge if not present.
        try:
            _p = _urlparse.urlparse(oauth_url)
            _qs = _urlparse.parse_qs(_p.query, keep_blank_values=True)
            _modified = False

            # Patch redirect_to if needed
            _redirect_to_list = _qs.get("redirect_to")
            _current_rt: str | None = (_redirect_to_list or [""])[0] or None  # type: ignore[list-item]
            if _current_rt and _current_rt != redirect_uri:
                _qs["redirect_to"] = [redirect_uri]
                _modified = True
                logger.info("Patching redirect_to", from_=_current_rt, to=redirect_uri)

            # Inject PKCE params if not present (required for PKCE flow)
            if "code_challenge" not in _qs:
                _qs["code_challenge"] = [self._code_challenge]
                _qs["code_challenge_method"] = ["S256"]
                _modified = True
                logger.info("Injected PKCE code_challenge into OAuth URL")

            if _modified:
                oauth_url = _urlparse.urlunparse(
                    _p._replace(query=_urlparse.urlencode(_qs, doseq=True))
                )
                logger.info("OAuth URL patched", url_preview=oauth_url[:200])
        except Exception as _patch_err:
            logger.warning("Could not patch OAuth URL", error=str(_patch_err))

        # ── 2. Open URL in Chrome / default browser ──────────────────────
        try:
            # Try Chrome by name first, fall back to system default
            chrome_opened = False
            for browser_name in ("chrome", "google-chrome", "chromium"):
                try:
                    browser = webbrowser.get(browser_name)
                    browser.open(oauth_url)
                    chrome_opened = True
                    logger.info("Opened OAuth URL in browser", browser=browser_name)
                    break
                except Exception:
                    continue
            if not chrome_opened:
                webbrowser.open(oauth_url)
                logger.info("Opened OAuth URL in default browser")
        except Exception as exc:
            server.stop()
            self.error.emit(f"Could not open browser: {exc}")
            return

        # ── 3. Wait for callback (up to 5 minutes), checking for cancellation ──
        logger.info("Waiting for OAuth callback…")
        result = None
        remaining = 300.0
        while remaining > 0 and not self.isInterruptionRequested():
            chunk = min(1.0, remaining)
            result = server.wait_for_token(timeout=chunk)
            if result is not None:
                break
            remaining -= chunk
        server.stop()

        if not result:
            self.error.emit(
                "Google sign-in timed out or was cancelled.\n"
                "Please try again."
            )
            return

        # ── 3a. Check for OAuth error response ───────────────────────────
        if result.get("error"):
            error_msg = result.get("error_description") or result.get("error")
            logger.warning("OAuth error from provider", error=error_msg)
            self.error.emit(f"Google sign-in failed:\n{error_msg}")
            return

        # ── 3b. PKCE flow: exchange the one-time code for tokens ─────────
        if "pkce_code" in result and not result.get("access_token"):
            pkce_code = result["pkce_code"]  # result narrowed above
            logger.info("PKCE code received — exchanging for tokens")

            # Try direct Supabase exchange first (works without backend)
            _sb_direct = _os.environ.get("LIVE_TRANSLATE_SUPABASE_URL", "").rstrip("/")
            _sb_anon = _os.environ.get("LIVE_TRANSLATE_SUPABASE_ANON_KEY", "")
            _exchanged = False

            if _sb_direct and _sb_anon:
                try:
                    with httpx.Client(timeout=15.0) as client:
                        exc_resp = client.post(
                            f"{_sb_direct}/auth/v1/token?grant_type=pkce",
                            json={
                                "auth_code": pkce_code,
                                "code_verifier": self._code_verifier,
                            },
                            headers={
                                "apikey": _sb_anon,
                                "Authorization": f"Bearer {_sb_anon}",
                                "Content-Type": "application/json",
                            },
                        )
                        exc_resp.raise_for_status()
                        result = exc_resp.json()
                        _exchanged = True
                        logger.info("PKCE exchange via Supabase direct succeeded")
                except Exception as exc:
                    logger.warning("Direct Supabase PKCE exchange failed", error=str(exc))

            if not _exchanged:
                # Fall back to backend exchange
                try:
                    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                        exc_resp = client.post(
                            f"{self._base_url}/api/v1/auth/oauth/google/exchange",
                            json={
                                "code": pkce_code,
                                "redirect_uri": redirect_uri,
                                "code_verifier": self._code_verifier,
                            },
                        )
                        exc_resp.raise_for_status()
                        result = exc_resp.json()
                        logger.info("PKCE exchange via backend succeeded")
                except httpx.HTTPStatusError as exc:
                    detail = ""
                    with contextlib.suppress(Exception):
                        detail = exc.response.json().get("detail", "")
                    logger.error(
                        "Backend PKCE exchange failed",
                        status=exc.response.status_code,
                        detail=detail,
                    )
                    self.error.emit(
                        f"Google sign-in failed during code exchange "
                        f"(HTTP {exc.response.status_code}).\n{detail or 'Please try again.'}"
                    )
                    return
                except httpx.ConnectError:
                    logger.error("Cannot connect to backend for PKCE exchange")
                    self.error.emit(
                        "Cannot connect to the server for code exchange.\n"
                        "Please check your internet connection."
                    )
                    return
                except Exception as exc:
                    logger.error("Backend PKCE exchange error", error=str(exc))
                    self.error.emit(f"Google sign-in failed during code exchange: {exc}")
                    return

        if not result:
            self.error.emit(
                "Google sign-in timed out or was cancelled.\n"
                "Please try again."
            )
            return
        result = cast(dict[str, Any], result)

        if not result.get("access_token"):
            self.error.emit(
                "Google sign-in timed out or was cancelled.\n"
                "Please try again."
            )
            return

        auth_result = result
        access_token: str = auth_result["access_token"]
        refresh_token: str = auth_result.get("refresh_token", "")
        logger.info("OAuth callback received — fetching profile")

        # ── 4. Fetch user profile (tier / usage) ─────────────────────────
        # Gracefully degrade — if the backend is unreachable (e.g. not yet
        # deployed, or running locally without the server), default to free-tier
        # limits so the user can still sign in and use the app.
        usage_data: dict = {}
        try:
            with httpx.Client(timeout=8.0, follow_redirects=False) as client:
                profile_resp = client.get(
                    f"{self._base_url}/api/v1/user/usage",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if (
                profile_resp.status_code == 200
                and "json" in profile_resp.headers.get("content-type", "")
            ):
                usage_data = profile_resp.json()
                logger.info(
                    "Loaded usage profile from backend",
                    url=f"{self._base_url}/api/v1/user/usage",
                    tier=usage_data.get("tier"),
                )
            else:
                raise ValueError(f"Unexpected response: {profile_resp.status_code}")
        except Exception as exc:
            logger.warning(
                "Could not load usage profile — using free-tier defaults",
                error=str(exc),
            )
            usage_data = _free_tier_defaults()

        # ── 5. Decode email / user_id from the JWT payload ───────────────
        email = ""
        user_id = ""
        try:
            payload_b64 = access_token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)  # Restore padding
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            email = payload.get("email", "")
            user_id = payload.get("sub", "")
        except Exception:
            pass  # Not critical

        logger.info("Google OAuth complete", user_id=user_id or "(unknown)", tier=usage_data.get("tier"))
        self.success.emit(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "tier": usage_data.get("tier", "free"),
                "usage": usage_data,
                "user_id": user_id,
                "email": email,
            }
        )


# ── Login Dialog ──────────────────────────────────────────────────────────────

class LoginDialog(QDialog):
    """
    Modal login / register dialog.

    On QDialog.Accepted, self.auth_response contains the full dict from the backend.
    """

    def __init__(self, settings: "AppSettings", parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.auth_response: dict = {}
        # Widgets set in _build_ui / _build_login_page / _build_register_page
        self._stack: QStackedWidget | None = None
        self._error_label: QLabel | None = None
        self._google_btn: QPushButton | None = None
        self._login_email: QLineEdit | None = None
        self._login_password: QLineEdit | None = None
        self._login_btn: QPushButton | None = None
        self._reg_email: QLineEdit | None = None
        self._reg_password: QLineEdit | None = None
        self._reg_password2: QLineEdit | None = None
        self._reg_btn: QPushButton | None = None
        self._worker: QThread | None = None
        self._oauth_worker: QThread | None = None

        self.setWindowTitle("Live Translate — Sign In")
        self.setMinimumWidth(380)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(32, 32, 32, 32)

        # Logo / title
        title = QLabel("Live Translate")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("Real-time AI dubbing")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #888; margin-bottom: 24px;")
        root.addWidget(subtitle)

        root.addSpacing(16)

        # Stacked widget: login page / register page
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_register_page())
        root.addWidget(self._stack)

        # Error label (shared between pages)
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #e05555; margin-top: 8px; font-size: 12px;")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.hide()
        root.addWidget(self._error_label)

    # ── Helper widget factories ───────────────────────────────────────────────

    def _input(self, placeholder: str, password: bool = False) -> QLineEdit:
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setMinimumHeight(36)
        if password:
            w.setEchoMode(QLineEdit.EchoMode.Password)
        return w

    def _primary_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(40)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(
            "QPushButton { background: #4f8cff; color: white; border-radius: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #3a72e0; }"
            "QPushButton:disabled { background: #555; color: #999; }"
        )
        return btn

    def _google_button(self) -> QPushButton:
        btn = QPushButton("  Sign in with Google")
        btn.setMinimumHeight(40)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(
            """
            QPushButton {
                background: #fff;
                color: #3c4043;
                border: 1px solid #dadce0;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
                padding-left: 8px;
                text-align: left;
            }
            QPushButton:hover { background: #f8f9fa; border-color: #bbb; }
            QPushButton:disabled { background: #555; color: #999; border-color: #444; }
            """
        )
        # Google "G" coloured text prepended as a unicode character trick
        btn.setText("\U0001F310  Sign in with Google")  # 🌐 fallback; real G logo not possible in pure Qt
        return btn

    def _divider(self, text: str = "or") -> QWidget:
        """Horizontal rule with centred text."""
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 8, 0, 8)
        for side in (True, False):
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("color: #444;")
            row.addWidget(line, 1)
            if side:
                lbl = QLabel(text)
                lbl.setStyleSheet("color: #888; padding: 0 8px; font-size: 12px;")
                row.addWidget(lbl)
        return w

    def _link_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFlat(True)
        btn.setStyleSheet("QPushButton { color: #4f8cff; text-decoration: underline; border: none; }")
        return btn

    # ── Page builders ─────────────────────────────────────────────────────────

    def _build_login_page(self) -> QWidget:
        assert self._stack is not None
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Google sign-in (primary CTA at top)
        self._google_btn = self._google_button()
        self._google_btn.clicked.connect(self._on_google_signin)
        layout.addWidget(self._google_btn)

        layout.addWidget(self._divider("or sign in with email"))

        # Email / password fields
        self._login_email = self._input("Email address")
        self._login_password = self._input("Password", password=True)
        self._login_password.returnPressed.connect(self._on_login)

        self._login_btn = self._primary_button("Sign In")
        self._login_btn.clicked.connect(self._on_login)

        layout.addWidget(self._login_email)
        layout.addWidget(self._login_password)
        layout.addSpacing(4)
        layout.addWidget(self._login_btn)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Don't have an account?"))
        to_register = self._link_button("Create one")
        stack = self._stack
        to_register.clicked.connect(lambda: stack.setCurrentIndex(1))
        bottom.addWidget(to_register)
        bottom.addStretch()
        layout.addLayout(bottom)

        web_row = QHBoxLayout()
        web_row.addWidget(QLabel("Prefer the web?"))
        signin_web_btn = self._link_button("Sign in or create account on the web")
        signin_web_btn.clicked.connect(self._open_signin_on_web)
        web_row.addWidget(signin_web_btn)
        web_row.addStretch()
        layout.addLayout(web_row)

        return page

    def _build_register_page(self) -> QWidget:
        assert self._stack is not None
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Google sign-up option on the register page too
        google_btn2 = self._google_button()
        google_btn2.clicked.connect(self._on_google_signin)
        layout.addWidget(google_btn2)

        layout.addWidget(self._divider("or create account with email"))

        self._reg_email = self._input("Email address")
        self._reg_password = self._input("Password (min 8 chars)", password=True)
        self._reg_password2 = self._input("Confirm password", password=True)
        self._reg_password2.returnPressed.connect(self._on_register)

        self._reg_btn = self._primary_button("Create Account — Free")
        self._reg_btn.clicked.connect(self._on_register)

        layout.addWidget(self._reg_email)
        layout.addWidget(self._reg_password)
        layout.addWidget(self._reg_password2)
        layout.addSpacing(4)
        layout.addWidget(self._reg_btn)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Already have an account?"))
        to_login = self._link_button("Sign in")
        stack = self._stack
        to_login.clicked.connect(lambda: stack.setCurrentIndex(0))
        bottom.addWidget(to_login)
        bottom.addStretch()
        layout.addLayout(bottom)

        web_row = QHBoxLayout()
        web_row.addWidget(QLabel("Prefer the web?"))
        signin_web_btn = self._link_button("Sign in or create account on the web")
        signin_web_btn.clicked.connect(self._open_signin_on_web)
        web_row.addWidget(signin_web_btn)
        web_row.addStretch()
        layout.addLayout(web_row)

        return page

    # ── Actions ───────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool) -> None:
        """Enable/disable all auth buttons and update labels.

        When going busy, hide any existing error.
        When un-busying, leave the error label alone so it stays visible.
        """
        assert self._login_btn is not None
        assert self._reg_btn is not None
        assert self._error_label is not None
        self._login_btn.setEnabled(not busy)
        self._reg_btn.setEnabled(not busy)
        if self._google_btn is not None:
            self._google_btn.setEnabled(not busy)
        self._login_btn.setText("Signing in…" if busy else "Sign In")
        self._reg_btn.setText("Creating account…" if busy else "Create Account — Free")
        if self._google_btn is not None:
            self._google_btn.setText(
                "Opening browser…" if busy else "\U0001F310  Sign in with Google"
            )
        # Only hide the error label when starting a new attempt,
        # NOT when re-enabling buttons after an error.
        if busy:
            self._error_label.hide()

    def _show_error(self, message: str) -> None:
        """Re-enable buttons then display the error message."""
        self._set_busy(False)           # Re-enable buttons first
        assert self._error_label is not None
        self._error_label.setText(message)
        self._error_label.show()        # Show AFTER _set_busy so it stays visible

    def _open_signin_on_web(self) -> None:
        """Open the official website sign-in page in the default browser."""
        webbrowser.open(self._settings.get_signin_url())

    def _on_auth_success(self, data: dict) -> None:
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            self._show_error("Invalid response from server (missing tokens).")
            return
        self.auth_response = data
        self._settings.set_auth_tokens(access, refresh)
        logger.info("Login successful", tier=data.get("tier"), user_id=data.get("user_id"))
        self.accept()

    # ── Email / password login ────────────────────────────────────────────────

    def _on_login(self) -> None:
        assert self._login_email is not None
        assert self._login_password is not None
        email = self._login_email.text().strip()
        password = self._login_password.text()
        if not email or not password:
            self._show_error("Please enter your email and password.")
            return

        self._set_busy(True)
        worker = _AuthWorker(self._settings.get_backend_url(), "login", email, password)
        worker.success.connect(self._on_auth_success)
        worker.error.connect(self._show_error)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker  # keep reference
        worker.start()

    def _on_register(self) -> None:
        assert self._reg_email is not None
        assert self._reg_password is not None
        assert self._reg_password2 is not None
        email = self._reg_email.text().strip()
        password = self._reg_password.text()
        password2 = self._reg_password2.text()

        if not email or not password:
            self._show_error("Please fill in all fields.")
            return
        if len(password) < 8:
            self._show_error("Password must be at least 8 characters.")
            return
        if password != password2:
            self._show_error("Passwords do not match.")
            return

        self._set_busy(True)
        worker = _AuthWorker(self._settings.get_backend_url(), "register", email, password)
        worker.success.connect(self._on_auth_success)
        worker.error.connect(self._show_error)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    # ── Google OAuth ──────────────────────────────────────────────────────────

    def _on_google_signin(self) -> None:
        """Start the Google OAuth flow: open Chrome and wait for the redirect."""
        assert self._error_label is not None
        self._set_busy(True)
        self._error_label.hide()

        worker = _OAuthWorker(self._settings.get_backend_url())
        worker.success.connect(self._on_auth_success)
        worker.error.connect(self._on_oauth_error)
        worker.finished.connect(worker.deleteLater)
        self._oauth_worker = worker  # keep reference
        worker.start()

    def _on_oauth_error(self, message: str) -> None:
        """Show the error and re-enable UI after a failed OAuth attempt."""
        logger.warning("Google OAuth error", message=message)
        self._show_error(message)

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Closing the login dialog without signing in exits the app."""
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.success.disconnect()
                worker.error.disconnect()
            except TypeError:
                pass
            worker.requestInterruption()
            worker.quit()
            worker.wait(2000)
        oauth_worker = getattr(self, "_oauth_worker", None)
        if oauth_worker is not None and oauth_worker.isRunning():
            try:
                oauth_worker.success.disconnect()
                oauth_worker.error.disconnect()
            except TypeError:
                pass
            oauth_worker.requestInterruption()
            oauth_worker.quit()
            oauth_worker.wait(2000)
        event.accept()
