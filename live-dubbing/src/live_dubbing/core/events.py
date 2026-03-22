"""
Event system for component communication.
"""

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


class EventType(Enum):
    """Types of events in the application."""

    # Application lifecycle
    APP_INITIALIZED = auto()
    APP_SHUTDOWN = auto()

    # Audio events
    AUDIO_DEVICE_CHANGED = auto()
    AUDIO_SESSION_DETECTED = auto()
    AUDIO_SESSION_LOST = auto()
    AUDIO_CAPTURE_STARTED = auto()
    AUDIO_CAPTURE_STOPPED = auto()
    AUDIO_LEVEL_UPDATE = auto()

    # Voice cloning events
    VOICE_CLONE_STARTED = auto()
    VOICE_CLONE_PROGRESS = auto()
    VOICE_CLONE_COMPLETED = auto()
    VOICE_CLONE_FAILED = auto()

    # Translation pipeline events
    TRANSLATION_STARTED = auto()
    TRANSLATION_STOPPED = auto()
    TRANSCRIPTION_UPDATE = auto()
    TRANSLATION_UPDATE = auto()
    TTS_STARTED = auto()
    TTS_COMPLETED = auto()

    # State changes
    STATE_CHANGED = auto()
    TRANSLATION_STATE_CHANGED = auto()

    # Errors
    ERROR_OCCURRED = auto()
    WARNING_OCCURRED = auto()
    PROCESS_LOOPBACK_FAILED = auto()
    AUTH_EXPIRED = auto()

    # Stats
    LATENCY_UPDATE = auto()
    STATS_UPDATE = auto()

    # Mic translation
    MIC_TRANSLATE_STARTED = auto()
    MIC_TRANSLATE_STOPPED = auto()
    MIC_TRANSCRIPTION_UPDATE = auto()
    MIC_TRANSLATION_UPDATE = auto()


@dataclass
class Event:
    """An event with type and associated data."""

    type: EventType
    data: dict[str, Any]

    def __post_init__(self) -> None:
        """Initialize default data dict if None."""
        if self.data is None:
            self.data = {}


class EventBus(QObject):
    """
    Thread-safe event bus for component communication.

    Uses Qt signals for thread-safe GUI updates while also
    supporting direct callback subscriptions.
    """

    # Qt signal for thread-safe event emission to GUI
    event_emitted = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._subscribers: dict[EventType, list[Callable[[Event], None]]] = {}
        self._lock = threading.Lock()

        # Connect internal signal to dispatch
        self.event_emitted.connect(self._dispatch_event)

    def subscribe(
        self, event_type: EventType, callback: Callable[[Event], None]
    ) -> Callable[[], None]:
        """
        Subscribe to an event type.

        Args:
            event_type: The type of event to subscribe to
            callback: Function to call when event occurs

        Returns:
            Unsubscribe function
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if event_type in self._subscribers:
                    with contextlib.suppress(ValueError):
                        self._subscribers[event_type].remove(callback)

        return unsubscribe

    def emit(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """
        Emit an event (thread-safe).

        Args:
            event_type: The type of event to emit
            data: Optional data associated with the event
        """
        event = Event(type=event_type, data=data or {})
        # Use Qt signal for thread-safe emission
        self.event_emitted.emit(event)

    def _dispatch_event(self, event: Event) -> None:
        """Dispatch event to all subscribers (called on main thread)."""
        with self._lock:
            subscribers = self._subscribers.get(event.type, []).copy()

        for callback in subscribers:
            try:
                callback(event)
            except Exception as e:
                # Log but don't propagate errors from callbacks
                import structlog
                logger = structlog.get_logger(__name__)
                logger.exception(
                    "Error in event callback",
                    event_type=event.type.name,
                    error=str(e),
                )

    def emit_error(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Convenience method to emit an error event."""
        self.emit(
            EventType.ERROR_OCCURRED,
            {"message": message, "details": details or {}},
        )

    def emit_warning(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Convenience method to emit a warning event."""
        self.emit(
            EventType.WARNING_OCCURRED,
            {"message": message, "details": details or {}},
        )

    def emit_state_change(self, old_state: Any, new_state: Any) -> None:
        """Convenience method to emit a state change event."""
        self.emit(
            EventType.STATE_CHANGED,
            {"old_state": old_state, "new_state": new_state},
        )
