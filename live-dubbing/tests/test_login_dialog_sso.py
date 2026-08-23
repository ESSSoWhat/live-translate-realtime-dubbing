"""Desktop login uses Live Translate SSO; Google is only on that website page."""

from pathlib import Path

_LOGIN_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "live_dubbing"
    / "gui"
    / "widgets"
    / "login_dialog.py"
)


def test_login_dialog_has_no_separate_google_button() -> None:
    src = _LOGIN_DIALOG.read_text(encoding="utf-8")
    assert "Sign in with Google" not in src
    assert "def _on_google_signin" not in src
    assert "class _OAuthWorker" not in src
    assert "def _on_wix_signin" in src
    assert "Sign in with livetranslate.net" in src
