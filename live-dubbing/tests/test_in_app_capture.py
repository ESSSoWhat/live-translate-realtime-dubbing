"""Tests for in-app capture process-loopback skip list."""

from live_dubbing.audio.in_app_capture import is_process_loopback_unreliable


def test_firefox_and_chrome_skip_process_loopback() -> None:
    assert is_process_loopback_unreliable("firefox.exe") is True
    assert is_process_loopback_unreliable("Firefox.exe") is True
    assert is_process_loopback_unreliable("firefox") is True
    assert is_process_loopback_unreliable("chrome.exe") is True
    assert is_process_loopback_unreliable("msedge.exe") is True


def test_games_use_process_loopback() -> None:
    assert is_process_loopback_unreliable("eldenring.exe") is False
    assert is_process_loopback_unreliable("firefox.exe.bak") is False
    assert is_process_loopback_unreliable(None) is False
    assert is_process_loopback_unreliable("") is False
