"""
Audio module - Capture, routing, and playback functionality.
"""

from live_dubbing.audio.capture import AudioCapture
from live_dubbing.audio.playback import AudioPlayback
from live_dubbing.audio.routing import VirtualAudioRouter
from live_dubbing.audio.session import AudioSessionEnumerator, AudioSessionInfo

__all__ = [
    "AudioCapture",
    "VirtualAudioRouter",
    "AudioPlayback",
    "AudioSessionEnumerator",
    "AudioSessionInfo",
]
