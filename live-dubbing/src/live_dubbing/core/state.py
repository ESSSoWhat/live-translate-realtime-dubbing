"""
Application state management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from live_dubbing.audio.session import AudioSessionInfo


class AppState(Enum):
    """Application lifecycle states."""

    INITIALIZING = auto()
    READY = auto()
    CONFIGURING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    ERROR = auto()


class TranslationState(Enum):
    """Translation pipeline states."""

    IDLE = auto()
    WAITING_FOR_AUDIO = auto()
    CLONING_VOICE = auto()
    TRANSLATING = auto()
    PAUSED = auto()
    ERROR = auto()


@dataclass
class VoiceCloneInfo:
    """Information about a cloned voice."""

    voice_id: str
    name: str
    created_at: datetime = field(default_factory=datetime.now)
    is_dynamic: bool = False
    sample_duration_sec: float = 0.0


@dataclass
class TranslationConfig:
    """Configuration for a translation session."""

    target_app: "AudioSessionInfo"  # Forward reference to avoid circular import
    source_language: str = "auto"
    target_language: str = "en"
    voice_clone: VoiceCloneInfo | None = None


@dataclass
class PipelineStats:
    """Statistics for the processing pipeline."""

    total_chunks_processed: int = 0
    total_audio_duration_sec: float = 0.0
    average_latency_ms: float = 0.0
    current_latency_ms: float = 0.0
    voice_clone_ready: bool = False
    is_speaking: bool = False
    last_transcription: str = ""
    last_translation: str = ""


@dataclass
class ApplicationStateSnapshot:
    """Complete snapshot of application state."""

    app_state: AppState = AppState.INITIALIZING
    translation_state: TranslationState = TranslationState.IDLE
    translation_config: TranslationConfig | None = None
    pipeline_stats: PipelineStats = field(default_factory=PipelineStats)
    vb_cable_installed: bool = False
    api_key_configured: bool = False
    error_message: str | None = None
