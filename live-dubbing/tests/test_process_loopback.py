"""Process loopback chooses the Firefox process instead of all system audio."""

from __future__ import annotations

from ctypes import wintypes

from live_dubbing.audio.process_loopback import (
    GUID,
    _iid_activate_handler,
    _iid_agile,
    _iid_unknown,
    activation_handler_accepts,
    loopback_pid_candidates,
)


def test_activation_handler_accepts_the_required_interfaces() -> None:
    assert activation_handler_accepts(_iid_unknown())
    assert activation_handler_accepts(_iid_activate_handler())
    assert activation_handler_accepts(_iid_agile())
    other = GUID(
        Data1=0x11111111,
        Data2=0x2222,
        Data3=0x3333,
        Data4=(wintypes.BYTE * 8)(1, 2, 3, 4, 5, 6, 7, 8),
    )
    assert activation_handler_accepts(other) is False


def test_firefox_child_can_fall_back_to_the_parent_process() -> None:
    names = {50: "firefox.exe", 10: "firefox.exe", 1: "explorer.exe"}
    parents = {50: (10, "firefox.exe"), 10: (1, "explorer.exe")}

    found = loopback_pid_candidates(
        50,
        name_of=names.__getitem__,
        parent_of=parents.get,
    )

    assert found == [50, 10]


def test_a_single_process_stays_the_only_candidate() -> None:
    found = loopback_pid_candidates(
        7,
        name_of=lambda _pid: "vlc.exe",
        parent_of=lambda _pid: (1, "explorer.exe"),
    )
    assert found == [7]
