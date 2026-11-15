"""The app list includes only programs that are outputting audio."""

from live_dubbing.audio.session import (
    AudioSessionInfo,
    audio_output_menu_labels,
    audio_session_state_is_active,
    select_current_audio_outputs,
)


def _session(
    pid: int,
    name: str,
    *,
    is_active: bool = True,
    is_muted: bool = False,
    volume: float = 1.0,
) -> AudioSessionInfo:
    return AudioSessionInfo(
        pid=pid,
        name=name,
        is_active=is_active,
        is_muted=is_muted,
        volume=volume,
    )


def test_inactive_and_system_processes_are_omitted() -> None:
    sessions = [
        _session(10, "Spotify.exe"),
        _session(11, "notepad.exe", is_active=False),
        _session(12, "svchost.exe"),
        _session(13, "LiveTranslate.exe"),
        _session(0, "Idle"),
    ]

    shown = select_current_audio_outputs(sessions)

    assert [item.name for item in shown] == ["Spotify.exe"]


def test_same_process_keeps_the_unmuted_session() -> None:
    sessions = [
        _session(21, "chrome.exe", is_muted=True, volume=0.2),
        _session(21, "chrome.exe", is_muted=False, volume=0.4),
    ]

    shown = select_current_audio_outputs(sessions)

    assert len(shown) == 1
    assert shown[0].pid == 21
    assert shown[0].is_muted is False


def test_two_processes_with_the_same_name_both_stay() -> None:
    sessions = [
        _session(22, "vlc.exe"),
        _session(21, "vlc.exe", is_muted=True),
    ]

    shown = select_current_audio_outputs(sessions)

    assert [item.pid for item in shown] == [21, 22]
    assert audio_output_menu_labels(shown) == [
        "vlc.exe (21) (Muted)",
        "vlc.exe (22)",
    ]


def test_own_process_id_is_excluded() -> None:
    sessions = [_session(7, "vlc.exe"), _session(8, "firefox.exe")]

    shown = select_current_audio_outputs(sessions, exclude_pids={7})

    assert [item.name for item in shown] == ["firefox.exe"]


def test_windows_active_state_is_streaming() -> None:
    assert audio_session_state_is_active(1) is True
    assert audio_session_state_is_active(0) is False
    assert audio_session_state_is_active(2) is False

    class _State:
        value = 1

    class _Inactive:
        value = 0

    assert audio_session_state_is_active(_State()) is True
    assert audio_session_state_is_active(_Inactive()) is False
    assert audio_session_state_is_active("AudioSessionState.Inactive") is False
    assert audio_session_state_is_active("AudioSessionState.Active") is True
