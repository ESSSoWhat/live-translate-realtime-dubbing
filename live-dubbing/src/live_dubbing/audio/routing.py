"""
Virtual audio device routing for per-app audio isolation.

Uses process loopback when available (no virtual cable). Falls back to
any compatible virtual cable (VB-Cable, VAC, etc.) when needed.
"""

from dataclasses import dataclass
from enum import Enum

import structlog

from live_dubbing.audio import virtual_cable

logger = structlog.get_logger(__name__)


class CaptureMode(Enum):
    """Audio capture mode."""

    VB_CABLE = "vb_cable"  # Per-app via virtual cable (VB-Cable, VAC, etc.)
    PROCESS_LOOPBACK = "process_loopback"  # Per-app via Windows API (no cable)
    SYSTEM_LOOPBACK = "system_loopback"  # All system audio
    NONE = "none"  # No capture configured


@dataclass
class VirtualDevice:
    """Information about a virtual audio device (for routing compatibility)."""

    name: str
    device_id: str
    input_id: str | None = None  # Apps output to this
    output_id: str | None = None  # We capture from this
    is_vb_cable: bool = False  # True for any virtual cable (kept for compat)


@dataclass
class RoutingConfig:
    """Configuration for audio routing."""

    virtual_device: VirtualDevice | None = None
    original_device_id: str | None = None
    requires_user_action: bool = False
    instructions: str | None = None
    capture_mode: CaptureMode = CaptureMode.VB_CABLE


class VirtualAudioRouter:
    """
    Manages virtual audio device detection and routing.

    Uses the virtual_cable module to detect any compatible cable
    (VB-Cable, VAC, etc.). Process loopback is preferred when available.
    """

    def __init__(self) -> None:
        self._virtual_devices: list[VirtualDevice] = []
        self._capture_device_id: str | None = None
        self._target_pid: int | None = None
        self._routing_active = False
        self._capture_mode: CaptureMode = CaptureMode.NONE
        self._default_output_device_id: str | None = None

    def detect_virtual_devices(self) -> list[VirtualDevice]:
        """
        Detect virtual audio devices (cables + VoiceMeeter).

        Uses virtual_cable for VB-Cable, VAC, and compatible products.
        """
        devices: list[VirtualDevice] = []
        cable = virtual_cable.get_virtual_cable()
        if cable:
            vdev = VirtualDevice(
                name=cable.name,
                device_id=cable.input_device_id,
                input_id=cable.input_device_id,
                output_id=cable.output_device_id,
                is_vb_cable=True,
            )
            devices.append(vdev)

        try:
            import re

            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                name = dev.get("name", "")
                if re.search(r"VoiceMeeter|Voicemeeter", name, re.IGNORECASE):
                    devices.append(
                        VirtualDevice(name=name, device_id=str(i), is_vb_cable=False)
                    )
            pa.terminate()
        except Exception as e:
            logger.debug("VoiceMeeter scan skipped", error=str(e))

        self._virtual_devices = devices
        logger.info("Detected virtual devices", count=len(devices))
        return devices

    def get_vb_cable(self) -> VirtualDevice | None:
        """
        Get virtual cable device if installed (VB-Cable, VAC, or compatible).

        Kept as get_vb_cable for API compatibility.
        """
        if not self._virtual_devices:
            self.detect_virtual_devices()
        for d in self._virtual_devices:
            if d.is_vb_cable and d.output_id:
                return d
        return None

    def is_vb_cable_installed(self) -> bool:
        """Check if any compatible virtual cable is installed."""
        return self.get_vb_cable() is not None

    async def route_app_to_virtual(self, pid: int) -> RoutingConfig:
        """
        Configure routing for per-app capture via virtual cable.

        Note: Windows doesn't allow programmatic per-app routing.
        User must set the app's output to the virtual cable in Sound settings.
        """
        cable = self.get_vb_cable()
        if not cable:
            raise RuntimeError(
                "No virtual cable found. Install VB-Cable or VAC (free): "
                "https://vb-audio.com/Cable/"
            )
        if not cable.output_id:
            raise RuntimeError("Virtual cable output device not found. Reinstall the cable driver.")

        self._capture_device_id = cable.output_id
        logger.info("Capture device set", device_id=self._capture_device_id)
        self._routing_active = True
        return RoutingConfig(
            virtual_device=cable,
            requires_user_action=True,
            instructions=self._generate_routing_instructions(pid, cable),
        )

    def configure_process_loopback(self, pid: int) -> RoutingConfig:
        """
        Configure native per-process loopback capture (Windows 10 21H2+).

        No VB-Cable or manual routing required.

        Args:
            pid: Target process ID to capture

        Returns:
            RoutingConfig for process loopback mode
        """
        from live_dubbing.audio.process_loopback import is_process_loopback_supported

        if not is_process_loopback_supported():
            raise RuntimeError(
                "Process loopback requires Windows 10 21H2 (build 20348) or Windows 11."
            )

        self._capture_mode = CaptureMode.PROCESS_LOOPBACK
        self._target_pid = pid
        self._capture_device_id = None
        self._routing_active = True

        logger.info(
            "Configured process loopback capture",
            pid=pid,
            mode=self._capture_mode.value,
        )

        return RoutingConfig(
            virtual_device=None,
            capture_mode=CaptureMode.PROCESS_LOOPBACK,
            requires_user_action=False,
            instructions="Process loopback capture active. No setup required.",
        )

    def is_process_loopback_supported(self) -> bool:
        """Check if native process loopback is supported on this system."""
        from live_dubbing.audio.process_loopback import is_process_loopback_supported

        return is_process_loopback_supported()

    def get_target_pid(self) -> int | None:
        """Get target process ID for process loopback mode."""
        return self._target_pid

    def _generate_routing_instructions(
        self, pid: int, cable: VirtualDevice
    ) -> str:
        """Generate user instructions for routing app to virtual cable."""
        return f"""
To isolate and capture ONLY the selected app's audio:

1. Right-click the speaker icon → "Open Sound settings"
2. Click "App volume and device preferences"
3. Find the target app → set its "Output" to your virtual cable
   (e.g. CABLE Input, Line 1, or {cable.name})

Only that app's audio will be captured. Other apps play to normal speakers.
"""

    async def restore_original_routing(self, pid: int) -> None:
        """
        Restore original audio routing for an application.

        Note: User must manually restore in Windows settings for VB-Cable mode.
        """
        self._routing_active = False
        self._target_pid = None
        logger.info("Routing deactivated", pid=pid)

    def get_capture_device_id(self) -> str | None:
        """
        Get the device ID to use for audio capture.

        Returns:
            Device ID string or None if not configured
        """
        return self._capture_device_id

    def get_capture_device_index(self) -> int | None:
        """
        Get the device index to use for audio capture.

        Returns:
            Device index integer or None if not configured
        """
        if self._capture_device_id:
            return int(self._capture_device_id)
        return None

    @property
    def is_routing_active(self) -> bool:
        """Check if routing is currently active."""
        return self._routing_active

    def get_setup_url(self) -> str:
        """Get URL to download a free virtual cable."""
        return virtual_cable.get_setup_url()

    def get_default_output_device(self) -> str | None:
        """
        Get the loopback device that corresponds to the default output device.

        Strategy:
        1. Find the default WASAPI output device (where the user hears audio).
        2. Find the ``[Loopback]`` device whose name matches that output device.
        3. Fall back to the first loopback device if no match is found.

        Returns:
            Device ID string or None if not found
        """
        try:
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()

            # Step 1 – identify the default WASAPI output device
            default_speakers = None
            try:
                wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_out_idx = wasapi_info.get("defaultOutputDevice")
                if default_out_idx is not None:
                    default_speakers = pa.get_device_info_by_index(int(default_out_idx))
                    logger.info(
                        "Default WASAPI output device",
                        name=default_speakers.get("name"),
                        index=default_out_idx,
                    )
            except Exception as exc:
                logger.warning("Could not determine default WASAPI output", error=str(exc))

            # Step 2 – use pyaudiowpatch loopback generator for reliable discovery
            first_loopback_id: str | None = None
            default_output_name = default_speakers.get("name", "") if default_speakers else ""
            for loopback in pa.get_loopback_device_info_generator():
                idx = loopback.get("index")
                name: str = loopback.get("name", "")
                if first_loopback_id is None:
                    first_loopback_id = str(idx)
                # Match: loopback name starts with output device (e.g. "Headphones... [Loopback]")
                if default_output_name and name.lower().startswith(default_output_name.lower()):
                    pa.terminate()
                    self._default_output_device_id = str(idx)
                    logger.info(
                        "Found matching loopback device",
                        name=name,
                        index=idx,
                    )
                    return self._default_output_device_id

            pa.terminate()

            # Step 3 – no name match; use first loopback device
            if first_loopback_id is not None:
                self._default_output_device_id = first_loopback_id
                logger.info(
                    "Using first loopback device",
                    index=first_loopback_id,
                )
                return self._default_output_device_id

        except ImportError:
            logger.error("pyaudiowpatch not installed")
        except Exception as e:
            logger.exception("Failed to get default output device", error=str(e))

        return None

    def get_loopback_device_or_default(self, device_id: str | None) -> str | None:
        """
        Return device_id if it's a valid loopback device, else default.

        Used when user selects a capture channel from the volume mixer device list.
        """
        if not device_id or not device_id.strip():
            return self.get_default_output_device()
        try:
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()
            idx = int(device_id)
            for loopback in pa.get_loopback_device_info_generator():
                if loopback.get("index") == idx:
                    pa.terminate()
                    return device_id
            pa.terminate()
        except (ValueError, ImportError, Exception):
            pass
        return self.get_default_output_device()

    def configure_system_loopback(
        self, capture_device_id: str | None = None
    ) -> RoutingConfig:
        """
        Configure system-wide loopback capture.

        Captures audio from the selected output device (volume mixer channel).
        If capture_device_id is set, uses that loopback; else default.

        Returns:
            RoutingConfig for system loopback mode
        """
        device_id = (
            self.get_loopback_device_or_default(capture_device_id)
            if capture_device_id
            else self.get_default_output_device()
        )

        if device_id is None:
            raise RuntimeError("No default output device found for loopback capture")

        self._capture_device_id = device_id
        self._capture_mode = CaptureMode.SYSTEM_LOOPBACK
        self._routing_active = True

        logger.info(
            "Configured system loopback capture",
            device_id=device_id,
            mode=self._capture_mode.value,
        )

        return RoutingConfig(
            virtual_device=None,
            capture_mode=CaptureMode.SYSTEM_LOOPBACK,
            requires_user_action=False,
            instructions="System loopback capture is active. All system audio will be captured.",
        )

    @property
    def capture_mode(self) -> CaptureMode:
        """Get current capture mode."""
        return self._capture_mode

    def set_capture_mode(self, mode: CaptureMode) -> None:
        """Set the capture mode."""
        self._capture_mode = mode
        logger.info("Capture mode set", mode=mode.value)

    def get_routing_status(self) -> dict:
        """
        Get current routing status for UI display.

        Returns:
            Dict with status info
        """
        vb_cable = self.get_vb_cable()
        return {
            "vb_cable_installed": vb_cable is not None,
            "vb_cable_ready": vb_cable is not None and vb_cable.output_id is not None,
            "process_loopback_supported": self.is_process_loopback_supported(),
            "capture_mode": self._capture_mode.value,
            "capture_device_id": self._capture_device_id,
            "target_pid": self._target_pid,
            "routing_active": self._routing_active,
        }
