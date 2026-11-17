"""
Status bar widget showing application state.
"""


from PyQt6.QtWidgets import (  # pylint: disable=no-name-in-module  # pyright: ignore[reportMissingImports]
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from live_dubbing.core.state import AppState, TranslationState
from live_dubbing.gui.theme import ACCENT, DANGER, MUTED, OK


class StatusIndicator(QFrame):
    """Small colored indicator dot."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._set_color("gray")

    def _set_color(self, color: str) -> None:
        """Set the indicator color."""
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {color};
                border-radius: 6px;
                border: 1px solid #1C5878;
            }}
            """
        )

    def set_state(self, state: str) -> None:
        """
        Set indicator state.

        Args:
            state: One of "idle", "active", "warning", "error"
        """
        colors = {
            "idle": MUTED,
            "active": OK,
            "warning": ACCENT,
            "error": DANGER,
            "processing": ACCENT,
        }
        self._set_color(colors.get(state, MUTED))


class StatusBar(QWidget):
    """
    Status bar showing current application state.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(15)

        # Status indicator
        self._indicator = StatusIndicator()
        layout.addWidget(self._indicator)

        # Status text
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._status_label)

        # Separator
        layout.addStretch()

        # Capture status (virtual cable for play-as-mic; updated from orchestrator)
        self._vb_label = QLabel("Capture: --")
        self._vb_label.setStyleSheet(f"font-size: 12px; color: {MUTED};")
        self._vb_label.setToolTip(
            "Virtual audio cable (e.g. VB-Cable) for play-as-mic routing. "
            "In-app loopback capture does not require this."
        )
        layout.addWidget(self._vb_label)

        # API status
        self._api_label = QLabel("API: --")
        self._api_label.setStyleSheet(f"font-size: 12px; color: {MUTED};")
        self._api_label.setToolTip("API key from sign-in or ELEVENLABS_API_KEY")
        layout.addWidget(self._api_label)

        # Latency
        self._latency_label = QLabel("Latency: --")
        self._latency_label.setStyleSheet(f"font-size: 12px; color: {MUTED};")
        self._latency_label.setToolTip("Round-trip processing latency")
        layout.addWidget(self._latency_label)

        # ElevenLabs credit
        self._credit_label = QLabel("Powered by ElevenLabs")
        self._credit_label.setStyleSheet(
            f"font-size: 10px; color: {MUTED}; font-style: italic;"
        )
        layout.addWidget(self._credit_label)

    def set_app_state(self, state: AppState) -> None:
        """Update display based on application state."""
        state_info = {
            AppState.INITIALIZING: ("Initializing...", "processing"),
            AppState.READY: ("Ready", "idle"),
            AppState.CONFIGURING: ("Configuring...", "processing"),
            AppState.RUNNING: ("Running", "active"),
            AppState.PAUSED: ("Paused", "warning"),
            AppState.STOPPING: ("Stopping...", "warning"),
            AppState.ERROR: ("Error", "error"),
        }

        text, indicator = state_info.get(state, ("Unknown", "idle"))
        self._status_label.setText(text)
        self._indicator.set_state(indicator)

    def set_translation_state(self, state: TranslationState) -> None:
        """Update display based on translation state."""
        state_info = {
            TranslationState.IDLE: "Idle",
            TranslationState.WAITING_FOR_AUDIO: "Waiting for audio...",
            TranslationState.CLONING_VOICE: "Cloning voice...",
            TranslationState.TRANSLATING: "Translating",
            TranslationState.PAUSED: "Paused",
            TranslationState.ERROR: "Error",
        }

        text = state_info.get(state, "Unknown")
        self._status_label.setText(text)

        # Update indicator for translation states
        if state == TranslationState.TRANSLATING:
            self._indicator.set_state("active")
        elif state == TranslationState.CLONING_VOICE:
            self._indicator.set_state("processing")
        elif state == TranslationState.ERROR:
            self._indicator.set_state("error")

    def set_vb_cable_status(self, installed: bool) -> None:
        """Update capture availability: virtual cable detected (True) or not (False).

        Name kept for API compatibility with existing callers.
        """
        if installed:
            self._vb_label.setText("Capture: Available")
            self._vb_label.setStyleSheet(f"font-size: 12px; color: {OK};")
        else:
            self._vb_label.setText("Capture: Unavailable")
            self._vb_label.setStyleSheet(f"font-size: 12px; color: {DANGER};")

    def set_api_status(self, configured: bool) -> None:
        """Update API key status."""
        if configured:
            self._api_label.setText("API: OK")
            self._api_label.setStyleSheet(f"font-size: 12px; color: {OK};")
        else:
            self._api_label.setText("API: Missing")
            self._api_label.setStyleSheet(f"font-size: 12px; color: {DANGER};")

    def set_latency(self, latency_ms: float) -> None:
        """Update latency display."""
        if latency_ms < 0:
            self._latency_label.setText("Latency: --")
            self._latency_label.setStyleSheet(f"font-size: 12px; color: {MUTED};")
        elif latency_ms < 1000:
            self._latency_label.setText(f"Latency: {latency_ms:.0f}ms")
            self._latency_label.setStyleSheet(f"font-size: 12px; color: {OK};")
        elif latency_ms < 2000:
            self._latency_label.setText(f"Latency: {latency_ms:.0f}ms")
            self._latency_label.setStyleSheet(f"font-size: 12px; color: {ACCENT};")
        else:
            self._latency_label.setText(f"Latency: {latency_ms:.0f}ms")
            self._latency_label.setStyleSheet(f"font-size: 12px; color: {DANGER};")
