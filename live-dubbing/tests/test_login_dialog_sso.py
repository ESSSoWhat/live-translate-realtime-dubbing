"""Desktop login dialog uses the SSO bridge for Google sign-in."""

from pathlib import Path

_LOGIN_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "live_dubbing"
    / "gui"
    / "widgets"
    / "login_dialog.py"
)


def test_google_signin_uses_desktop_sso_bridge() -> None:
    """In-dialog Google must not open a random localhost OAuth callback."""
    src = _LOGIN_DIALOG.read_text(encoding="utf-8")
    start = src.index("def _on_google_signin")
    end = src.index("def _on_oauth_error")
    body = src[start:end]
    assert "self._on_wix_signin()" in body
    assert "_OAuthWorker" not in body
