"""
Application selector widget for choosing audio source.
"""


import structlog
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from live_dubbing.audio.session import AudioSessionInfo

logger = structlog.get_logger(__name__)


class AppSelectorWidget(QWidget):
    """
    Widget for selecting an application to capture audio from.

    Displays a dropdown list of running applications with audio sessions.
    """

    # Signal emitted when an app is selected
    app_selected = pyqtSignal(object)  # AudioSessionInfo

    # Signal to refresh the app list
    refresh_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sessions: list[AudioSessionInfo] = []
        self._selected_session: AudioSessionInfo | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QLabel("Select Application")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        # App selector row
        selector_layout = QHBoxLayout()

        # Dropdown
        self._combo = QComboBox()
        self._combo.setMinimumWidth(250)
        self._combo.setMaxVisibleItems(10)  # Limit dropdown size
        self._combo.activated.connect(self._on_selection_changed)  # User-initiated only
        self._combo.setPlaceholderText("Select an application...")
        selector_layout.addWidget(self._combo, 1)

        # Refresh button
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        self._refresh_btn.setMaximumWidth(80)
        selector_layout.addWidget(self._refresh_btn)

        layout.addLayout(selector_layout)

        # Info label
        self._info_label = QLabel()
        self._info_label.setStyleSheet("color: gray; font-size: 11px;")
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        self._update_info_label()

    def set_sessions(self, sessions: list[AudioSessionInfo]) -> None:
        """
        Update the list of available audio sessions.

        Args:
            sessions: List of AudioSessionInfo objects
        """
        self._sessions = sessions

        # Remember current selection
        current_pid = self._selected_session.pid if self._selected_session else None

        # Update combo box
        self._combo.blockSignals(True)
        self._combo.clear()

        for session in sessions:
            display_name = session.name
            if session.is_muted:
                display_name += " (Muted)"
            self._combo.addItem(display_name, session)

        # Restore selection if possible
        if current_pid:
            for i, session in enumerate(sessions):
                if session.pid == current_pid:
                    self._combo.setCurrentIndex(i)
                    break

        self._combo.blockSignals(False)
        self._update_info_label()

    def get_selected_session(self) -> AudioSessionInfo | None:
        """Get the currently selected audio session.

        If nothing was explicitly selected but the combo box has items,
        return the currently displayed item.  This handles the common case
        where a single app is listed and the user clicks Start without
        first clicking on the dropdown.
        """
        if self._selected_session is not None:
            return self._selected_session

        # Fall back to whatever the combo box is currently showing
        idx = self._combo.currentIndex()
        if idx >= 0:
            session_data = self._combo.itemData(idx)
            if session_data is not None:
                self._selected_session = session_data
                return self._selected_session

        return None

    def _on_selection_changed(self, index: int) -> None:
        """Handle selection change in combo box."""
        try:
            if index >= 0:
                # Get session data from combo box item (safer than using index into list)
                session_data = self._combo.itemData(index)
                if session_data is not None:
                    self._selected_session = session_data
                    self.app_selected.emit(self._selected_session)
                else:
                    self._selected_session = None
            else:
                self._selected_session = None

            self._update_info_label()
        except Exception as e:
            import traceback
            print(f"ERROR in _on_selection_changed: {e}")
            traceback.print_exc()

    def _on_refresh_clicked(self) -> None:
        """Handle refresh button click."""
        self.refresh_requested.emit()

    def _update_info_label(self) -> None:
        """Update the info label based on current state."""
        if not self._sessions:
            self._info_label.setText(
                "No applications with audio detected. Click Refresh to scan."
            )
        elif self._selected_session:
            self._info_label.setText(
                f"Selected: {self._selected_session.name} (PID: {self._selected_session.pid})"
            )
        else:
            self._info_label.setText(
                f"{len(self._sessions)} application(s) with audio available."
            )

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the widget."""
        self._combo.setEnabled(enabled)
        self._refresh_btn.setEnabled(enabled)
