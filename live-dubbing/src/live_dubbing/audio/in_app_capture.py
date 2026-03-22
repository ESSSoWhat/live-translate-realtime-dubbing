"""
In-app software capture — built-in audio capture without external virtual cables.

Provides system loopback and process loopback capture. No VB-Cable, VAC, or
other external drivers required. System loopback captures all system audio;
process loopback (Windows 10 21H2+) captures a single app.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CaptureConfig:
    """Configuration for in-app capture."""

    device_id: str | None = None  # For system loopback (WASAPI loopback device)
    pid: int | None = None  # For process loopback (per-app capture)
    mode: str = "system_loopback"  # system_loopback | process_loopback
    capture_device_id: str | None = None  # Selected volume mixer channel


def is_process_loopback_supported() -> bool:
    """Check if native process loopback is available (Windows 10 21H2+)."""
    try:
        from live_dubbing.audio.process_loopback import (
            is_process_loopback_supported as _check_plb,
        )

        return _check_plb()
    except ImportError:
        return False


def get_capture_channels() -> list[tuple[str, str]]:
    """
    List capture channels — each maps to a volume mixer output device.

    Returns (device_id, display_name) tuples. Apps routed to that device
    in Windows Sound settings will be captured from the corresponding channel.

    Returns:
        List of (device_id, display_name) e.g. [("122", "Headphones"), ...]
    """
    channels: list[tuple[str, str]] = []
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        for loopback in pa.get_loopback_device_info_generator():
            idx = loopback.get("index")
            name: str = loopback.get("name", "")
            # Strip "[Loopback]" for display — matches volume mixer device names
            display = name.replace(" [Loopback]", "").strip() or name
            channels.append((str(idx), display))
        pa.terminate()
        logger.info("Capture channels enumerated", count=len(channels))
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Failed to enumerate capture channels", error=str(e))
    return channels


def get_system_loopback_device_id(capture_device_id: str | None = None) -> str | None:
    """Get the device ID for system loopback capture."""
    try:
        from live_dubbing.audio.routing import VirtualAudioRouter

        router = VirtualAudioRouter()
        if capture_device_id:
            return router.get_loopback_device_or_default(capture_device_id)
        return router.get_default_output_device()
    except Exception as e:
        logger.warning("Could not get system loopback device", error=str(e))
        return None


def get_capture_config(
    *,
    use_system_only: bool = False,
    target_pid: int | None = None,
    capture_device_id: str | None = None,
) -> CaptureConfig:
    """
    Get capture configuration for in-app software capture.

    Uses system loopback or process loopback. Never requires VB-Cable.

    Args:
        use_system_only: If True, always use system loopback (all system audio).
        target_pid: Process ID for per-app capture when use_system_only is False.

    Returns:
        CaptureConfig with device_id (system) or pid (process loopback).
    """
    if use_system_only:
        device_id = get_system_loopback_device_id(capture_device_id)
        if device_id:
            return CaptureConfig(
                device_id=device_id,
                mode="system_loopback",
                capture_device_id=capture_device_id,
            )
        raise RuntimeError("No output device found for loopback capture.")

    if target_pid and is_process_loopback_supported():
        return CaptureConfig(pid=target_pid, mode="process_loopback")

    device_id = get_system_loopback_device_id(capture_device_id)
    if device_id:
        return CaptureConfig(
            device_id=device_id,
            mode="system_loopback",
            capture_device_id=capture_device_id,
        )
    raise RuntimeError("No audio capture available. Check your default output device.")


def configure_routing(
    config: CaptureConfig,
    router: object,
) -> None:
    """Configure the router for the given capture config."""
    from live_dubbing.audio.routing import CaptureMode, VirtualAudioRouter

    if not isinstance(router, VirtualAudioRouter):
        return

    if config.mode == "process_loopback" and config.pid:
        router.configure_process_loopback(config.pid)
    else:
        router.configure_system_loopback(config.capture_device_id)
