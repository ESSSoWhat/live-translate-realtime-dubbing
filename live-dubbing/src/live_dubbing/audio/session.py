"""
Audio session enumeration and control using pycaw.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

import psutil
import structlog

logger = structlog.get_logger(__name__)

# System processes that should be filtered out from the app list
SYSTEM_PROCESS_NAMES: set[str] = {
    # Windows core processes
    "System",
    "svchost.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "dwm.exe",
    "smss.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "taskhostw.exe",
    "ctfmon.exe",
    "conhost.exe",
    "dllhost.exe",
    "WmiPrvSE.exe",
    "spoolsv.exe",
    "SearchIndexer.exe",
    "SecurityHealthService.exe",
    "MsMpEng.exe",
    "NisSrv.exe",
    # Windows shell/UI
    "explorer.exe",
    "SearchHost.exe",
    "ShellExperienceHost.exe",
    "RuntimeBroker.exe",
    "ApplicationFrameHost.exe",
    "SystemSettings.exe",
    "StartMenuExperienceHost.exe",
    "TextInputHost.exe",
    "LockApp.exe",
    "WidgetService.exe",
    "Widgets.exe",
    # This app and the interpreter it runs under
    "python.exe",
    "pythonw.exe",
    "python3.exe",
    "LiveTranslate.exe",
    # Common background services
    "OneDrive.exe",
    "SearchApp.exe",
    "backgroundTaskHost.exe",
    "CompPkgSrv.exe",
    "audiodg.exe",
    "WUDFHost.exe",
    "dasHost.exe",
    "SettingSyncHost.exe",
    "UserOOBEBroker.exe",
}


# IAudioSessionControl::GetState — the client is streaming audio.
_AUDIO_SESSION_ACTIVE = 1


def audio_session_state_is_active(state: object) -> bool:
    """True when a Windows audio session is currently streaming output."""
    value = getattr(state, "value", state)
    try:
        return int(value) == _AUDIO_SESSION_ACTIVE
    except (TypeError, ValueError):
        return str(state).rsplit(".", 1)[-1].lower() == "active"


def select_current_audio_outputs(
    sessions: list[AudioSessionInfo],
    *,
    exclude_pids: set[int] | None = None,
) -> list[AudioSessionInfo]:
    """Keep every process that is outputting audio now.

    Inactive, expired, system, and this app's own processes are left out.
    Two processes with the same executable stay as separate rows, because
    capture follows the process id. Several sessions on one process collapse
    to the unmuted session.
    """
    excluded = exclude_pids or set()
    chosen: dict[int, AudioSessionInfo] = {}
    for session in sessions:
        if session.pid in excluded or session.pid <= 0 or not session.name:
            continue
        if not session.is_active or session.name in SYSTEM_PROCESS_NAMES:
            continue
        current = chosen.get(session.pid)
        if current is None or _prefer_audio_session(session, current):
            chosen[session.pid] = session
    return sorted(chosen.values(), key=lambda item: (item.name.lower(), item.pid))


def audio_output_menu_labels(sessions: list[AudioSessionInfo]) -> list[str]:
    """Labels for the app menu. Repeated names include the process id."""
    counts: dict[str, int] = {}
    for session in sessions:
        key = session.name.lower()
        counts[key] = counts.get(key, 0) + 1
    labels: list[str] = []
    for session in sessions:
        label = session.name
        if counts[session.name.lower()] > 1:
            label = f"{session.name} ({session.pid})"
        if session.is_muted:
            label += " (Muted)"
        labels.append(label)
    return labels


def _prefer_audio_session(
    candidate: AudioSessionInfo,
    current: AudioSessionInfo,
) -> bool:
    """True when *candidate* is the better row for the same program."""
    if current.is_muted and not candidate.is_muted:
        return True
    if candidate.is_muted and not current.is_muted:
        return False
    return candidate.volume > current.volume


@dataclass
class AudioSessionInfo:
    """Information about a Windows audio session."""

    pid: int
    name: str
    icon_path: str | None = None
    is_active: bool = True
    volume: float = 1.0
    is_muted: bool = False

    def __hash__(self) -> int:
        """Return hash based on process ID."""
        return hash(self.pid)

    def __eq__(self, other: object) -> bool:
        """Check equality based on process ID."""
        if isinstance(other, AudioSessionInfo):
            return self.pid == other.pid
        return False


class AudioSessionEnumerator:
    """
    Enumerates audio sessions on Windows using pycaw.

    This class provides functionality to list all applications
    currently producing audio output.
    """

    def __init__(self) -> None:
        self._sessions: list[AudioSessionInfo] = []

    def get_active_sessions(self) -> list[AudioSessionInfo]:
        """Windows audio sessions.

        ``is_active`` is true only while that session is streaming audio.
        Inactive and expired sessions are included so callers can tell them
        apart; the app list uses :meth:`get_current_audio_outputs`.
        """
        try:
            from pycaw.pycaw import AudioUtilities

            sessions = []

            # Get all audio sessions
            audio_sessions = AudioUtilities.GetAllSessions()

            for session in audio_sessions:
                try:
                    # Get process info
                    if session.Process:
                        pid = session.Process.pid
                        name = session.Process.name()

                        # Get volume interface
                        volume_interface = session.SimpleAudioVolume
                        current_volume = (
                            volume_interface.GetMasterVolume()
                            if volume_interface
                            else 1.0
                        )
                        is_muted = (
                            volume_interface.GetMute() if volume_interface else False
                        )

                        # Try to get icon path
                        icon_path = None
                        with contextlib.suppress(Exception):
                            if session.Process:
                                icon_path = session.Process.exe()

                        try:
                            is_active = audio_session_state_is_active(session.State)
                        except Exception:
                            is_active = False

                        session_info = AudioSessionInfo(
                            pid=pid,
                            name=name,
                            icon_path=icon_path,
                            is_active=is_active,
                            volume=current_volume,
                            is_muted=bool(is_muted),
                        )
                        sessions.append(session_info)

                except Exception as e:
                    logger.debug("Failed to get session info", error=str(e))
                    continue

            self._sessions = sessions
            logger.info("Enumerated audio sessions", count=len(sessions))
            return sessions

        except ImportError:
            logger.error("pycaw not installed")
            return []
        except Exception as e:
            logger.exception("Failed to enumerate audio sessions", error=str(e))
            return []

    def get_session_by_pid(self, pid: int) -> AudioSessionInfo | None:
        """
        Get audio session for a specific process ID.

        Args:
            pid: Process ID to look for

        Returns:
            AudioSessionInfo if found, None otherwise
        """
        for session in self._sessions:
            if session.pid == pid:
                return session

        # Refresh and try again
        self.get_active_sessions()
        for session in self._sessions:
            if session.pid == pid:
                return session

        return None

    def refresh(self) -> list[AudioSessionInfo]:
        """Refresh the session list."""
        return self.get_current_audio_outputs()

    def get_current_audio_outputs(
        self,
        *,
        exclude_pids: set[int] | None = None,
    ) -> list[AudioSessionInfo]:
        """Applications that are streaming audio right now."""
        return select_current_audio_outputs(
            self.get_active_sessions(),
            exclude_pids=exclude_pids,
        )

    def _is_system_process(self, name: str) -> bool:
        """
        Check if process is a system process that shouldn't be listed.

        Args:
            name: Process name to check

        Returns:
            True if this is a system process, False otherwise
        """
        return name in SYSTEM_PROCESS_NAMES

    def get_all_audio_capable_processes(self) -> list[AudioSessionInfo]:
        """
        Get all running processes that could potentially produce audio.

        This uses psutil to enumerate all running processes and filters
        out system processes.

        Returns:
            List of AudioSessionInfo for all user processes
        """
        processes = []
        seen_names: set[str] = set()

        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                # Get process info dict
                proc_info = proc.as_dict(attrs=['pid', 'name', 'exe'])
                name = proc_info.get('name')
                if not name:
                    continue

                # Skip system processes
                if self._is_system_process(name):
                    continue

                # Skip duplicates (same process name)
                if name.lower() in seen_names:
                    continue
                seen_names.add(name.lower())

                # Create session info
                session_info = AudioSessionInfo(
                    pid=proc_info.get('pid', 0),
                    name=name,
                    icon_path=proc_info.get('exe'),
                    is_active=False,  # Not currently producing audio
                    volume=1.0,
                    is_muted=False,
                )
                processes.append(session_info)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.debug("Failed to get process info", error=str(e))
                continue

        return processes

    def get_sessions_combined(self) -> list[AudioSessionInfo]:
        """
        Get both active audio sessions and all running audio-capable processes.

        This provides a complete view of all apps that could be captured,
        with active audio sessions preferred (since they have volume info).

        Returns:
            Combined and sorted list of all sessions
        """
        # Get active audio sessions (these have real volume info)
        active_sessions = {s.pid: s for s in self.get_active_sessions()}

        # Get all running processes
        all_processes = self.get_all_audio_capable_processes()

        # Merge: prefer active sessions (they have accurate volume/mute info)
        for proc in all_processes:
            if proc.pid not in active_sessions:
                active_sessions[proc.pid] = proc

        # Sort by name for easier finding
        combined = sorted(active_sessions.values(), key=lambda s: s.name.lower())

        logger.info(
            "Combined audio sessions",
            active_count=len([s for s in combined if s.is_active]),
            total_count=len(combined),
        )

        return combined


class AudioSessionController:
    """
    Controls audio session volume and mute state.
    """

    def __init__(self) -> None:
        self._session_cache: dict[int, Any] = {}

    def _get_session(self, pid: int) -> Any:
        """Get pycaw session for a PID."""
        try:
            from pycaw.pycaw import AudioUtilities

            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                if session.Process and session.Process.pid == pid:
                    return session
            return None
        except Exception as e:
            logger.exception("Failed to get session", pid=pid, error=str(e))
            return None

    def mute_session(self, pid: int) -> bool:
        """
        Mute a specific audio session.

        Args:
            pid: Process ID of the session to mute

        Returns:
            True if successful, False otherwise
        """
        try:
            session = self._get_session(pid)
            if session and session.SimpleAudioVolume:
                session.SimpleAudioVolume.SetMute(1, None)
                logger.info("Muted session", pid=pid)
                return True
            return False
        except Exception as e:
            logger.exception("Failed to mute session", pid=pid, error=str(e))
            return False

    def unmute_session(self, pid: int) -> bool:
        """
        Unmute a specific audio session.

        Args:
            pid: Process ID of the session to unmute

        Returns:
            True if successful, False otherwise
        """
        try:
            session = self._get_session(pid)
            if session and session.SimpleAudioVolume:
                session.SimpleAudioVolume.SetMute(0, None)
                logger.info("Unmuted session", pid=pid)
                return True
            return False
        except Exception as e:
            logger.exception("Failed to unmute session", pid=pid, error=str(e))
            return False

    def set_volume(self, pid: int, volume: float) -> bool:
        """
        Set volume for a specific audio session.

        Args:
            pid: Process ID of the session
            volume: Volume level (0.0 to 1.0)

        Returns:
            True if successful, False otherwise
        """
        try:
            volume = max(0.0, min(1.0, volume))  # Clamp to valid range
            session = self._get_session(pid)
            if session and session.SimpleAudioVolume:
                session.SimpleAudioVolume.SetMasterVolume(volume, None)
                logger.info("Set session volume", pid=pid, volume=volume)
                return True
            return False
        except Exception as e:
            logger.exception("Failed to set volume", pid=pid, error=str(e))
            return False

    def get_volume(self, pid: int) -> float | None:
        """
        Get current volume of an audio session.

        Args:
            pid: Process ID of the session

        Returns:
            Volume level (0.0 to 1.0) or None if not found
        """
        try:
            session = self._get_session(pid)
            if session and session.SimpleAudioVolume:
                vol: float = session.SimpleAudioVolume.GetMasterVolume()
                return vol
            return None
        except Exception as e:
            logger.exception("Failed to get volume", pid=pid, error=str(e))
            return None
