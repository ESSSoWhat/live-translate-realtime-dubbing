"""
Audio module - Capture, routing, and playback functionality.
"""

from live_dubbing.audio.capture import AudioCapture
from live_dubbing.audio.playback import AudioPlayback
from live_dubbing.audio.routing import VirtualAudioRouter
from live_dubbing.audio.session import AudioSessionEnumerator, AudioSessionInfo
from live_dubbing.audio.virtual_cable import (
    VirtualCableDevice,
    get_virtual_cable,
    is_virtual_cable_available,
)

__all__ = [
    "AudioCapture",
    "VirtualAudioRouter",
    "VirtualCableDevice",
    "AudioPlayback",
    "AudioSessionEnumerator",
    "AudioSessionInfo",
    "get_virtual_cable",
    "is_virtual_cable_available",
]
