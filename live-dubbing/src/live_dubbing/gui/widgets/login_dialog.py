"""
Login dialog — shown at startup if no valid auth token is found.

Supports:
  - Email / password login and registration.
  - Sign in with Live Translate (website SSO, including Google on that page).

On QDialog.Accepted:
    self.auth_response  — full dict from the backend (access_token, refresh_token,
                          tier, usage, user_id, email).
"""
# Pylint: widgets are assigned in _build_ui; docstrings on thread run() are minimal.
# pylint: disable=attribute-defined-outside-init,missing-function-docstring

from __future__ import annotations

import base64
import json as _json
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from live_dubbing.gui.theme import brand_mark
from live_dubbing.updates import (
    PatchDownloadThread,
    UpdateCheckThread,
    UpdateOffer,
    install_dir,
    launch_patch_helper,
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


# ── Wix SSO worker ─────────────────────────────────────────────────────────────


class _WixSsoWorker(QThread):
    """Drive the Wix SSO flow: opens livetranslate.net/login, waits for callback with API key."""

    success = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, base_url: str, settings: "AppSettings") -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._settings = settings

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            logger.exception("Unhandled error in _WixSsoWorker", error=str(exc))
            self.error.emit(f"Unexpected error during Wix sign-in: {exc}")

    def _run(self) -> None:
        from live_dubbing.services.oauth_callback_server import OAuthCallbackServer
        import secrets as _secrets

        server = OAuthCallbackServer()
        port = server.start()
        redirect_uri = server.redirect_uri
        desktop_session = _secrets.token_urlsafe(24)
        logger.info("Wix SSO callback server started", port=port, session_prefix=desktop_session[:8])

        sso_url = self._settings.get_wix_sso_entry_url(
            redirect_uri, desktop_session=desktop_session
        )
        logger.info("Opening desktop SSO bridge", url_preview=sso_url[:120])

        try:
            webbrowser.open(sso_url)
        except Exception as exc:
            server.stop()
            self.error.emit(f"Could not open browser: {exc}")
            return

        logger.info("Waiting for Wix SSO (handoff poll + localhost callback)…")
        result = None
        remaining = 900.0
        # Prefer short non-blocking polls. wait_for_token must not starve handoff checks —
        # Wix cannot redirect to localhost, so backend handoff is the real path.
        with httpx.Client(timeout=5.0) as client:
            while remaining > 0 and not self.isInterruptionRequested():
                try:
                    r = client.get(
                        f"{self._base_url}/api/v1/auth/desktop-handoff/{desktop_session}"
                    )
                    if r.status_code == 200:
                        payload = r.json()
                        if payload.get("ready") and payload.get("api_key"):
                            result = {"api_key": payload["api_key"]}
                            logger.info(
                                "Wix SSO handoff received",
                                session_prefix=desktop_session[:8],
                            )
                            break
                except Exception as exc:
                    logger.debug("Handoff poll error", error=str(exc))

                # Fallback: localhost redirect (often blocked by Wix)
                result = server.wait_for_token(timeout=0.5)
                if result is not None:
                    break
                remaining -= 0.5
        server.stop()

        if not result:
            self.error.emit(
                "Sign-in timed out. After logging in on the website, wait for "
                "“Signed in! You can close this tab”, then try again if needed."
            )
            return

        if result.get("error"):
            self.error.emit(f"Wix sign-in failed: {result.get('error_description') or result.get('error')}")
            return

        api_key = result.get("api_key") or result.get("access_token")
        if not api_key:
            self.error.emit("No sign-in token received from the website. Please try again.")
            return

        logger.info("Wix SSO callback received — validating API key")
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{self._base_url}/api/v1/user/me", headers={"Authorization": f"Bearer {api_key}"})
            if r.status_code != 200:
                self.error.emit("Invalid API key received. Please try again.")
                return
            data = r.json()
            data["_api_key"] = api_key
            logger.info("Wix SSO complete", user_id=data.get("user_id"), tier=data.get("tier"))
            self.success.emit(data)
        except Exception as exc:
            logger.exception("API key validation failed", error=str(exc))
            self.error.emit("Could not validate API key. Check your connection.")


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
        self._login_email: QLineEdit | None = None
        self._login_password: QLineEdit | None = None
        self._login_btn: QPushButton | None = None
        self._reg_email: QLineEdit | None = None
        self._reg_password: QLineEdit | None = None
        self._reg_password2: QLineEdit | None = None
        self._reg_btn: QPushButton | None = None
        self._worker: QThread | None = None
        self._wix_sso_worker: QThread | None = None
        self._wix_btn: QPushButton | None = None
        self._wix_btn_reg: QPushButton | None = None
        self._cancel_wait_btn: QPushButton | None = None
        self._update_offer: UpdateOffer | None = None
        self._update_check: UpdateCheckThread | None = None
        self._patch_thread: PatchDownloadThread | None = None
        self.restarting_for_update = False

        self.setWindowTitle("Live Translate — Sign In")
        self.setMinimumWidth(380)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._build_ui()
        self._start_update_check()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(32, 32, 32, 32)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark = brand_mark(72)
        if not mark.isNull():
            logo.setPixmap(mark)
        root.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Live Translate")
        title.setObjectName("wordmark")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.DemiBold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("Real-time AI dubbing")
        subtitle.setObjectName("tagline")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)

        self._update_box = QWidget()
        update_layout = QVBoxLayout(self._update_box)
        update_layout.setContentsMargins(0, 12, 0, 0)
        update_layout.setSpacing(8)
        self._update_label = QLabel("")
        self._update_label.setObjectName("muted")
        self._update_label.setWordWrap(True)
        self._update_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_btn = QPushButton("Install update")
        self._update_btn.setObjectName("secondary")
        self._update_btn.clicked.connect(self._on_install_update)
        self._update_progress = QProgressBar()
        self._update_progress.setRange(0, 100)
        self._update_progress.hide()
        update_layout.addWidget(self._update_label)
        update_layout.addWidget(self._update_btn)
        update_layout.addWidget(self._update_progress)
        self._update_box.hide()
        root.addWidget(self._update_box)

        root.addSpacing(16)

        # Stacked widget: login page / register page
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_register_page())
        root.addWidget(self._stack)

        # Error label (shared between pages)
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #ff8b8b; margin-top: 8px; font-size: 12px;")
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
        return btn

    def _wix_button(self) -> QPushButton:
        """Primary CTA: one-click SSO via livetranslate.net."""
        btn = QPushButton()
        btn.setMinimumHeight(40)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setText("Sign in with livetranslate.net")
        return btn

    def _divider(self, text: str = "or") -> QWidget:
        """Horizontal rule with centred text."""
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 8, 0, 8)
        for side in (True, False):
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("color: #1c5878;")
            row.addWidget(line, 1)
            if side:
                lbl = QLabel(text)
                lbl.setObjectName("muted")
                row.addWidget(lbl)
        return w

    def _link_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFlat(True)
        btn.setObjectName("link")
        return btn

    # ── Page builders ─────────────────────────────────────────────────────────

    def _build_login_page(self) -> QWidget:
        assert self._stack is not None
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Primary: Wix site sign-in (same Google login as the website — no GCP client needed)
        self._wix_btn = self._wix_button()
        self._wix_btn.clicked.connect(self._on_wix_signin)
        layout.addWidget(self._wix_btn)
        wix_tip = QLabel(
            "Opens a sign-in page — use Google or email. "
            "The app continues automatically when the browser finishes."
        )
        wix_tip.setWordWrap(True)
        wix_tip.setObjectName("muted")
        layout.addWidget(wix_tip)

        self._cancel_wait_btn = self._link_button("Cancel waiting")
        self._cancel_wait_btn.hide()
        self._cancel_wait_btn.clicked.connect(self._on_cancel_wix_wait)
        layout.addWidget(self._cancel_wait_btn)

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

        return page

    def _on_wix_signin(self) -> None:
        """Start the Wix SSO flow: open browser and wait for redirect with API key."""
        assert self._error_label is not None
        self._set_busy(True)
        self._error_label.hide()
        if self._cancel_wait_btn is not None:
            self._cancel_wait_btn.show()

        worker = _WixSsoWorker(self._settings.get_backend_url(), self._settings)
        worker.success.connect(self._on_wix_sso_success)
        worker.error.connect(self._on_wix_sso_error)
        worker.finished.connect(self._on_wix_sso_finished)
        worker.finished.connect(worker.deleteLater)
        self._wix_sso_worker = worker
        worker.start()

    def _on_cancel_wix_wait(self) -> None:
        """Stop waiting for the browser SSO callback."""
        worker = self._wix_sso_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
        self._show_error("Sign-in cancelled.")

    def _on_wix_sso_finished(self) -> None:
        if self._cancel_wait_btn is not None:
            self._cancel_wait_btn.hide()
        # Always clear busy if the dialog is still open (error/cancel/timeout).
        # Success calls accept() which closes the dialog.
        if self.isVisible():
            self._set_busy(False)

    def _on_wix_sso_success(self, data: dict) -> None:
        """Handle successful Wix SSO sign-in."""
        if self._cancel_wait_btn is not None:
            self._cancel_wait_btn.hide()
        api_key = data.get("_api_key", "")
        if not api_key:
            self._show_error("No API key received from the website. Please try again.")
            return
        self._settings.set_auth_tokens(api_key, "")
        user_id = str(data.get("user_id", ""))
        tier = str(data.get("tier", "free"))
        self._settings.set_cached_user_info(user_id, tier)
        usage = data.get("usage") or _free_tier_defaults()
        self.auth_response = {
            "user_id": user_id,
            "email": data.get("email", ""),
            "tier": tier,
            "usage": usage,
            "access_token": api_key,
            "refresh_token": "",
        }
        logger.info("Wix SSO login accepted", user_id=user_id or "(unknown)", tier=tier)
        self._set_busy(False)
        self.accept()

    def _on_wix_sso_error(self, message: str) -> None:
        """Handle Wix SSO error."""
        if self._cancel_wait_btn is not None:
            self._cancel_wait_btn.hide()
        logger.warning("Wix SSO error", message=message)
        self._show_error(message)

    def _build_register_page(self) -> QWidget:
        assert self._stack is not None
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # SSO (same flow as login)
        self._wix_btn_reg = self._wix_button()
        self._wix_btn_reg.clicked.connect(self._on_wix_signin)
        layout.addWidget(self._wix_btn_reg)

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

        return page

    # ── Actions ───────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool) -> None:
        """Enable/disable auth buttons and update labels."""
        assert self._login_btn is not None
        assert self._reg_btn is not None
        assert self._error_label is not None
        self._login_btn.setEnabled(not busy)
        self._reg_btn.setEnabled(not busy)
        if self._wix_btn is not None:
            self._wix_btn.setEnabled(not busy)
            self._wix_btn.setText("Waiting for browser…" if busy else "Sign in with livetranslate.net")
        if self._wix_btn_reg is not None:
            self._wix_btn_reg.setEnabled(not busy)
            self._wix_btn_reg.setText("Waiting for browser…" if busy else "Sign in with livetranslate.net")
        self._login_btn.setText("Signing in…" if busy else "Sign In")
        self._reg_btn.setText("Creating account…" if busy else "Create Account — Free")
        if busy:
            self._error_label.hide()

    def _show_error(self, message: str) -> None:
        """Re-enable buttons then display the error message."""
        self._set_busy(False)           # Re-enable buttons first
        assert self._error_label is not None
        self._error_label.setText(message)
        self._error_label.show()        # Show AFTER _set_busy so it stays visible

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

    def _start_update_check(self) -> None:
        """Look for a patch while the sign-in form is open."""
        worker = UpdateCheckThread(self._settings.get_backend_url())
        worker.found.connect(self._on_update_found)
        worker.finished.connect(worker.deleteLater)
        self._update_check = worker
        worker.start()

    def _on_update_found(self, offer: object) -> None:
        if not isinstance(offer, UpdateOffer):
            return
        self._update_offer = offer
        note = f" {offer.notes}" if offer.notes else ""
        self._update_label.setText(f"Version {offer.latest} is available.{note}")
        self._update_box.show()

    def _on_install_update(self) -> None:
        """Download the published patch and restart into it."""
        offer = self._update_offer
        if offer is None:
            return
        if install_dir() is None:
            self._show_error(
                "This copy is running from source. Open the installed Live Translate "
                "app and sign in to apply the patch."
            )
            return
        answer = QMessageBox.question(
            self,
            "Install update",
            f"Install version {offer.latest} now?\n\n"
            "Live Translate will close and reopen when the patch is applied.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._update_btn.setEnabled(False)
        self._update_progress.setValue(0)
        self._update_progress.show()
        thread = PatchDownloadThread(offer)
        thread.progress.connect(self._update_progress.setValue)
        thread.failed.connect(self._on_patch_failed)
        thread.ready.connect(self._on_patch_ready)
        thread.finished.connect(thread.deleteLater)
        self._patch_thread = thread
        thread.start()

    def _on_patch_failed(self, message: str) -> None:
        self._update_btn.setEnabled(True)
        self._update_progress.hide()
        self._show_error(message)

    def _on_patch_ready(self, path: str) -> None:
        try:
            launch_patch_helper(Path(path))
        except Exception as exc:
            self._on_patch_failed(str(exc))
            return
        self.restarting_for_update = True
        QApplication.quit()

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
        for extra in (self._update_check, self._patch_thread):
            if extra is not None and extra.isRunning():
                extra.requestInterruption()
                extra.quit()
                extra.wait(1500)
        event.accept()
