"""Settings dialog (API key configuration removed — not user-accessible)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from live_dubbing.config.settings import AppSettings


class SettingsDialog(QDialog):
    """Placeholder settings dialog; API configuration is not exposed in the UI."""

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._saved = False

        self.setWindowTitle("Settings")
        self.setMinimumWidth(320)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(
            QLabel("API keys are configured via sign-in or environment variables.")
        )
        layout.addWidget(QLabel("No settings are available in this dialog."))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @property
    def was_saved(self) -> bool:
        """Whether the user saved; always False as there is nothing to save."""
        return self._saved
