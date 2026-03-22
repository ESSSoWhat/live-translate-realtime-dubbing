"""
Virtual audio device routing for per-app audio isolation.
"""

import re
from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class CaptureMode(Enum):
    """Audio capture mode."""

    VB_CABLE = "vb_cable"  # Per-app capture via VB-Cable
    PROCESS_LOOPBACK = "process_loopback"  # Per-app capture via Windows API (no VB-Cable)
    SYSTEM_LOOPBACK = "system_loopback"  # Capture all system audio
    NONE = "none"  # No capture configured


@dataclass
class VirtualDevice:
    """Information about a virtual audio device."""

    name: str
    device_id: str
    input_id: str | None = None  # For routing audio TO the device
    output_id: str | None = None  # For capturing audio FROM the device
    is_vb_cable: bool = False


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

    This class detects VB-Audio Virtual Cable and helps configure
    audio routing for per-application capture.
    """

    # Known virtual device name patterns
    VB_CABLE_PATTERNS = [
        r"CABLE Input",
        r"CABLE Output",
        r"VB-Audio Virtual Cable",
        r"VB-Cable",
    ]

    VOICEMEETER_PATTERNS = [
        r"VoiceMeeter",
        r"Voicemeeter Input",
        r"Voicemeeter Output",
    ]

    def __init__(self) -> None:
        self._virtual_devices: list[VirtualDevice] = []
        self._capture_device_id: str | None = None
        self._target_pid: int | None = None
        self._routing_active = False
        self._capture_mode: CaptureMode = CaptureMode.NONE
        self._default_output_device_id: str | None = None

    def detect_virtual_devices(self) -> list[VirtualDevice]:
        """
        Detect installed virtual audio devices using pyaudiowpatch.

        Uses pyaudiowpatch (not sounddevice) to ensure device indices
        are compatible with the capture module.

        Returns:
            List of detected virtual audio devices
        """
        devices = []

        try:
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()

            vb_cable_input = None
            vb_cable_output = None

            for i in range(pa.get_device_count()):
                device = pa.get_device_info_by_index(i)
                name = device.get("name", "")
                device_id = str(i)

                # Check for VB-Cable
                for pattern in self.VB_CABLE_PATTERNS:
                    if re.search(pattern, name, re.IGNORECASE):
                        is_input = device.get("maxInputChannels", 0) > 0
                        is_output = device.get("maxOutputChannels", 0) > 0

                        if "Input" in name or (is_output and not is_input):
                            # This is where apps OUTPUT to (virtual speaker)
                            vb_cable_input = VirtualDevice(
                                name=name,
                                device_id=device_id,
                                input_id=device_id,
                                is_vb_cable=True,
                            )
                        elif "Output" in name or (is_input and not is_output):
                            # This is where we CAPTURE from (virtual mic)
                            vb_cable_output = VirtualDevice(
                                name=name,
                                device_id=device_id,
                                output_id=device_id,
                                is_vb_cable=True,
                            )
                        break

                # Check for VoiceMeeter
                for pattern in self.VOICEMEETER_PATTERNS:
                    if re.search(pattern, name, re.IGNORECASE):
                        virtual_device = VirtualDevice(
                            name=name,
                            device_id=device_id,
                            is_vb_cable=False,
                        )
                        devices.append(virtual_device)
                        break

            pa.terminate()

            # Combine VB-Cable input/output into single device
            if vb_cable_input and vb_cable_output:
                combined = VirtualDevice(
                    name="VB-Audio Virtual Cable",
                    device_id=vb_cable_input.device_id,
                    input_id=vb_cable_input.device_id,
                    output_id=vb_cable_output.device_id,
                    is_vb_cable=True,
                )
                devices.insert(0, combined)  # Prefer VB-Cable
            elif vb_cable_input:
                devices.insert(0, vb_cable_input)
            elif vb_cable_output:
                devices.insert(0, vb_cable_output)

            self._virtual_devices = devices
            logger.info("Detected virtual audio devices", count=len(devices))

            for device in devices:
                logger.debug(
                    "Virtual device",
                    name=device.name,
                    input_id=device.input_id,
                    output_id=device.output_id,
                )

            return devices

        except ImportError:
            logger.error("pyaudiowpatch not installed")
            return []
        except Exception as e:
            logger.exception("Failed to detect virtual devices", error=str(e))
            return []

    def get_vb_cable(self) -> VirtualDevice | None:
        """
        Get VB-Audio Virtual Cable device if installed.

        Returns:
            VirtualDevice for VB-Cable or None if not found
        """
        if not self._virtual_devices:
            self.detect_virtual_devices()

        for device in self._virtual_devices:
            if device.is_vb_cable:
                return device
        return None

    def is_vb_cable_installed(self) -> bool:
        """Check if VB-Audio Virtual Cable is installed."""
        return self.get_vb_cable() is not None

    async def route_app_to_virtual(self, pid: int) -> RoutingConfig:
        """
        Configure routing for a specific application.

        Note: Windows doesn't allow programmatic per-app audio routing.
        This method returns instructions for manual configuration.

        Args:
            pid: Process ID of the application

        Returns:
            RoutingConfig with setup instructions
        """
        vb_cable = self.get_vb_cable()

        if not vb_cable:
            raise RuntimeError("VB-Audio Virtual Cable not installed")

        # Ensure we have the output device ID for capture
        if not vb_cable.output_id:
            raise RuntimeError(
                "VB-Audio Virtual Cable output device not found. "
                "Please ensure VB-Cable is properly installed and restart the app."
            )

        # Set the capture device
        self._capture_device_id = vb_cable.output_id
        logger.info("Capture device set", device_id=self._capture_device_id)

        # Generate instructions for user
        instructions = self._generate_routing_instructions(pid, vb_cable)

        self._routing_active = True

        return RoutingConfig(
            virtual_device=vb_cable,
            requires_user_action=True,
            instructions=instructions,
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
        self, pid: int, vb_cable: VirtualDevice
    ) -> str:
        """Generate user instructions for audio routing."""
        return """
To isolate and capture ONLY the selected app's audio:

1. Right-click the speaker icon in the Windows taskbar
2. Select "Open Sound settings"
3. Scroll down and click "App volume and device preferences"
4. Find the target application in the list
5. Change its "Output" dropdown to "CABLE Input (VB-Audio Virtual Cable)"

Only that app's audio will be captured for translation.
Other applications continue playing to your normal speakers.
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
        """Get URL to download VB-Audio Virtual Cable."""
        return "https://vb-audio.com/Cable/"

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

            # Step 1 – identify the default WASAPI output device name
            default_output_name: str | None = None
            try:
                wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_out_idx = wasapi_info.get("defaultOutputDevice")
                if default_out_idx is not None:
                    default_out = pa.get_device_info_by_index(int(default_out_idx))
                    default_output_name = default_out.get("name", "")
                    logger.info(
                        "Default WASAPI output device",
                        name=default_output_name,
                        index=default_out_idx,
                    )
            except Exception as exc:
                logger.warning("Could not determine default WASAPI output", error=str(exc))

            # Step 2 – collect all loopback devices; try to match by name
            first_loopback_id: str | None = None
            for i in range(pa.get_device_count()):
                device = pa.get_device_info_by_index(i)
                name: str = device.get("name", "")
                if device.get("maxInputChannels", 0) > 0 and "[loopback]" in name.lower():
                    if first_loopback_id is None:
                        first_loopback_id = str(i)

                    # Match: the loopback device name is the output device
                    # name followed by " [Loopback]"
                    if default_output_name and name.lower().startswith(
                        default_output_name.lower()
                    ):
                        pa.terminate()
                        self._default_output_device_id = str(i)
                        logger.info(
                            "Found matching loopback device",
                            name=name,
                            index=i,
                        )
                        return self._default_output_device_id

            pa.terminate()

            # Step 3 – no name match; fall back to first loopback device
            if first_loopback_id is not None:
                self._default_output_device_id = first_loopback_id
                logger.warning(
                    "No exact loopback match; using first loopback device",
                    index=first_loopback_id,
                )
                return self._default_output_device_id

        except ImportError:
            logger.error("pyaudiowpatch not installed")
        except Exception as e:
            logger.exception("Failed to get default output device", error=str(e))

        return None

    def configure_system_loopback(self) -> RoutingConfig:
        """
        Configure system-wide loopback capture as fallback.

        This captures ALL system audio, not just one app.

        Returns:
            RoutingConfig for system loopback mode
        """
        # Get default output device for loopback
        device_id = self.get_default_output_device()

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
