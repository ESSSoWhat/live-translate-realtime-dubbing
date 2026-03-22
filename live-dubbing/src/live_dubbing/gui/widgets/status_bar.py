"""
Status bar widget showing application state.
"""


from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from live_dubbing.core.state import AppState, TranslationState


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
                border: 1px solid #333;
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
            "idle": "#666",
            "active": "#4CAF50",
            "warning": "#FFC107",
            "error": "#F44336",
            "processing": "#2196F3",
        }
        self._set_color(colors.get(state, "#666"))


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

        # Capture status (in-app software capture)
        self._vb_label = QLabel("Capture: Built-in")
        self._vb_label.setStyleSheet("font-size: 12px; color: #4CAF50;")
        self._vb_label.setToolTip("In-app software capture (system/process loopback)")
        layout.addWidget(self._vb_label)

        # API status
        self._api_label = QLabel("API: --")
        self._api_label.setStyleSheet("font-size: 12px; color: #aaa;")
        self._api_label.setToolTip("API key from sign-in or ELEVENLABS_API_KEY")
        layout.addWidget(self._api_label)

        # Latency
        self._latency_label = QLabel("Latency: --")
        self._latency_label.setStyleSheet("font-size: 12px; color: #aaa;")
        self._latency_label.setToolTip("Round-trip processing latency")
        layout.addWidget(self._latency_label)

        # ElevenLabs credit
        self._credit_label = QLabel("Powered by ElevenLabs")
        self._credit_label.setStyleSheet(
            "font-size: 10px; color: #999; font-style: italic;"
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
        """Update capture status (kept for API compat; always built-in now)."""
        self._vb_label.setText("Capture: Built-in")
        self._vb_label.setStyleSheet("font-size: 12px; color: #4CAF50;")

    def set_api_status(self, configured: bool) -> None:
        """Update API key status."""
        if configured:
            self._api_label.setText("API: OK")
            self._api_label.setStyleSheet("font-size: 12px; color: #4CAF50;")
        else:
            self._api_label.setText("API: Missing")
            self._api_label.setStyleSheet("font-size: 12px; color: #F44336;")

    def set_latency(self, latency_ms: float) -> None:
        """Update latency display."""
        if latency_ms < 0:
            self._latency_label.setText("Latency: --")
            self._latency_label.setStyleSheet("font-size: 12px; color: #aaa;")
        elif latency_ms < 1000:
            self._latency_label.setText(f"Latency: {latency_ms:.0f}ms")
            self._latency_label.setStyleSheet("font-size: 12px; color: #4CAF50;")
        elif latency_ms < 2000:
            self._latency_label.setText(f"Latency: {latency_ms:.0f}ms")
            self._latency_label.setStyleSheet("font-size: 12px; color: #FFC107;")
        else:
            self._latency_label.setText(f"Latency: {latency_ms:.0f}ms")
            self._latency_label.setStyleSheet("font-size: 12px; color: #F44336;")
