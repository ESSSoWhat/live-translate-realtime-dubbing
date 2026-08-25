"""
Audio session enumeration and control using pycaw.
"""

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
    "Idle",
    "Registry",
    "Secure System",
    "Memory Compression",
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
    "ShellHost.exe",
    "RuntimeBroker.exe",
    "ApplicationFrameHost.exe",
    "SystemSettings.exe",
    "StartMenuExperienceHost.exe",
    "TextInputHost.exe",
    "LockApp.exe",
    "WidgetService.exe",
    "Widgets.exe",
    # Python (filter out ourselves)
    "python.exe",
    "pythonw.exe",
    "python3.exe",
    # Common background / audio-graph services
    "OneDrive.exe",
    "SearchApp.exe",
    "backgroundTaskHost.exe",
    "CompPkgSrv.exe",
    "audiodg.exe",
    "WUDFHost.exe",
    "dasHost.exe",
    "SettingSyncHost.exe",
    "UserOOBEBroker.exe",
    "RtkAudUService64.exe",
    "RtkAudUService.exe",
    "NahimicService.exe",
    "NahimicSvc64.exe",
    "NahimicSvc32.exe",
    "WavesSvc64.exe",
    "DolbyDAX2.exe",
    "DolbyDAXAPI.exe",
    "IntelAudioService.exe",
    "AMDNoiseSuppression.exe",
    "AMDFDPP.exe",
}

_SYSTEM_PROCESS_NAMES_LOWER: frozenset[str] = frozenset(
    name.lower() for name in SYSTEM_PROCESS_NAMES
)

# Browsers and players that process-loopback can capture even when WASAPI
# does not expose their session (common for Chrome/Firefox on some devices).
_KNOWN_MEDIA_PROCESSES: frozenset[str] = frozenset(
    {
        "chrome.exe",
        "firefox.exe",
        "msedge.exe",
        "brave.exe",
        "opera.exe",
        "vivaldi.exe",
        "discord.exe",
        "spotify.exe",
        "vlc.exe",
        "mpv.exe",
        "wmplayer.exe",
        "steam.exe",
        "obs64.exe",
        "obs.exe",
        "slack.exe",
        "zoom.exe",
        "teams.exe",
        "telegram.exe",
    }
)


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

    @staticmethod
    def _session_state(session: Any) -> int:
        """WASAPI state: 0 Inactive, 1 Active, 2 Expired, -1 unknown."""
        with contextlib.suppress(Exception):
            return int(session.State)
        return -1

    @staticmethod
    def _session_peak(session: Any) -> float:
        """Peak meter in 0..1, or 0 when unavailable."""
        try:
            from pycaw.api.endpointvolume import IAudioMeterInformation

            meter = session._ctl.QueryInterface(IAudioMeterInformation)
            return float(meter.GetPeakValue())
        except Exception:
            return 0.0

    @classmethod
    def _session_has_audio(cls, session: Any) -> bool:
        """True when the session exists on an endpoint and is not expired.

        Chrome/Firefox often report Inactive (0) while playing, so we keep
        those. Expired sessions are dropped.
        """
        state = cls._session_state(session)
        if state == 2:
            return False
        if state == 1 or cls._session_peak(session) > 0.0001:
            return True
        return state != 2

    @staticmethod
    def _is_system_sounds(session: Any) -> bool:
        """True for the Windows System Sounds session (no app to capture)."""
        with contextlib.suppress(Exception):
            if int(session.ProcessId) == 0:
                return True
        with contextlib.suppress(Exception):
            # S_OK (0) means this *is* the system-sounds session
            if int(session._ctl.IsSystemSoundsSession()) == 0:
                return True
        return False

    @staticmethod
    def _resolve_session_identity(session: Any) -> tuple[int, str, str | None]:
        """Return pid, process name, and exe path; tolerate AccessDenied."""
        pid = 0
        with contextlib.suppress(Exception):
            pid = int(session.ProcessId)
        if pid <= 0:
            return 0, "", None

        name = ""
        icon_path: str | None = None
        with contextlib.suppress(Exception):
            proc = session.Process
            if proc is not None:
                with contextlib.suppress(Exception):
                    name = proc.name() or ""
                with contextlib.suppress(Exception):
                    icon_path = proc.exe()
        if not name:
            with contextlib.suppress(Exception):
                name = psutil.Process(pid).name() or ""
        if not name:
            display = ""
            with contextlib.suppress(Exception):
                display = session.DisplayName or ""
            name = (
                display
                if display and not display.startswith("@")
                else f"PID {pid}"
            )
        if not icon_path:
            with contextlib.suppress(Exception):
                icon_path = psutil.Process(pid).exe()
        return pid, name, icon_path

    def _iter_wasapi_sessions(self) -> list[Any]:
        """WASAPI sessions on every active render device, not just the default."""
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioSession, AudioUtilities

        with contextlib.suppress(Exception):
            import comtypes

            comtypes.CoInitialize()

        from pycaw.api.audiopolicy import IAudioSessionControl2

        collected: list[Any] = []
        devices = []
        with contextlib.suppress(Exception):
            devices = AudioUtilities.GetAllDevices(
                data_flow=EDataFlow.eRender.value,
                device_state=DEVICE_STATE.ACTIVE.value,
            )

        managers = []
        for device in devices or []:
            with contextlib.suppress(Exception):
                managers.append(device.AudioSessionManager)

        if not managers:
            with contextlib.suppress(Exception):
                mgr = AudioUtilities.GetAudioSessionManager()
                if mgr is not None:
                    managers.append(mgr)

        seen_ctl: set[int] = set()
        for mgr in managers:
            try:
                enumerator = mgr.GetSessionEnumerator()
                count = enumerator.GetCount()
                for i in range(count):
                    ctl = enumerator.GetSession(i)
                    if ctl is None:
                        continue
                    ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                    if ctl2 is None:
                        continue
                    key = id(ctl2)
                    if key in seen_ctl:
                        continue
                    seen_ctl.add(key)
                    collected.append(AudioSession(ctl2))
            except Exception as e:
                logger.debug("Failed to enumerate sessions on device", error=str(e))
                continue

        return collected

    def get_active_sessions(self) -> list[AudioSessionInfo]:
        """
        Get apps that have a live WASAPI audio session.

        Scans every active playback device. Includes Active sessions and
        Inactive (not Expired) app sessions — browsers often stay Inactive
        while playing. System sounds and OS audio-graph processes are omitted.

        Returns:
            List of AudioSessionInfo objects for sessions with audio
        """
        try:
            sessions: list[AudioSessionInfo] = []
            seen_pids: set[int] = set()

            for session in self._iter_wasapi_sessions():
                try:
                    if self._is_system_sounds(session):
                        continue
                    if not self._session_has_audio(session):
                        continue

                    pid, name, icon_path = self._resolve_session_identity(session)
                    if pid <= 0 or not name:
                        continue
                    if self._is_system_process(name) or pid in seen_pids:
                        continue

                    volume_interface = None
                    with contextlib.suppress(Exception):
                        volume_interface = session.SimpleAudioVolume
                    current_volume = 1.0
                    is_muted = False
                    if volume_interface:
                        with contextlib.suppress(Exception):
                            current_volume = volume_interface.GetMasterVolume()
                        with contextlib.suppress(Exception):
                            is_muted = bool(volume_interface.GetMute())

                    is_playing = (
                        self._session_state(session) == 1
                        or self._session_peak(session) > 0.0001
                    )
                    sessions.append(
                        AudioSessionInfo(
                            pid=pid,
                            name=name,
                            icon_path=icon_path,
                            is_active=is_playing,
                            volume=current_volume,
                            is_muted=is_muted,
                        )
                    )
                    seen_pids.add(pid)

                except Exception as e:
                    logger.debug("Failed to get session info", error=str(e))
                    continue

            sessions = self._collapse_sessions_by_name(sessions)
            self._add_running_media_apps(sessions, seen_pids)
            sessions.sort(key=lambda s: (not s.is_active, s.name.lower()))
            self._sessions = sessions
            logger.info(
                "Enumerated audio sessions",
                count=len(sessions),
                playing=sum(1 for s in sessions if s.is_active),
                names=[s.name for s in sessions],
            )
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
        return self.get_active_sessions()

    def _is_system_process(self, name: str) -> bool:
        """
        Check if process is a system process that shouldn't be listed.

        Args:
            name: Process name to check

        Returns:
            True if this is a system process, False otherwise
        """
        return name.lower() in _SYSTEM_PROCESS_NAMES_LOWER

    @staticmethod
    def _collapse_sessions_by_name(
        sessions: list[AudioSessionInfo],
    ) -> list[AudioSessionInfo]:
        """Keep one row per process name; prefer the session that is playing."""
        by_name: dict[str, AudioSessionInfo] = {}
        for session in sessions:
            key = session.name.lower()
            existing = by_name.get(key)
            if existing is None:
                by_name[key] = session
                continue
            prefer_playing = session.is_active and not existing.is_active
            prefer_lower_pid = (
                session.is_active == existing.is_active and session.pid < existing.pid
            )
            if prefer_playing or prefer_lower_pid:
                by_name[key] = session
        return list(by_name.values())

    def _add_running_media_apps(
        self,
        sessions: list[AudioSessionInfo],
        seen_pids: set[int],
    ) -> None:
        """Include running browsers/players even if WASAPI hid their session."""
        seen_names = {s.name.lower() for s in sessions}
        best_by_name: dict[str, AudioSessionInfo] = {}
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = proc.info
                name = info.get("name") or ""
                key = name.lower()
                if key not in _KNOWN_MEDIA_PROCESSES:
                    continue
                if key in seen_names:
                    continue
                pid = int(info.get("pid") or 0)
                if pid <= 0 or pid in seen_pids:
                    continue
                candidate = AudioSessionInfo(
                    pid=pid,
                    name=name,
                    icon_path=info.get("exe"),
                    is_active=False,
                    volume=1.0,
                    is_muted=False,
                )
                existing = best_by_name.get(key)
                if existing is None or pid < existing.pid:
                    best_by_name[key] = candidate
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        for extra in best_by_name.values():
            sessions.append(extra)
            seen_pids.add(extra.pid)
            logger.info("Added running media app without WASAPI session", name=extra.name)

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
