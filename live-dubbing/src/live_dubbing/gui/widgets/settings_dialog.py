"""Settings dialog."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from live_dubbing.config.settings import AppSettings

_PLACEHOLDER = "••••••••••••"


class SettingsDialog(QDialog):
    """Settings dialog with API mode option."""

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._saved = False

        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(
            QLabel("API keys are configured via sign-in or environment variables.")
        )

        form = QFormLayout()
        self._elevenlabs_input = QLineEdit()
        self._elevenlabs_input.setPlaceholderText("Paste for direct API mode")
        self._elevenlabs_input.setEchoMode(QLineEdit.EchoMode.Password)
        if self._settings.get_elevenlabs_api_key():
            self._elevenlabs_input.setText(_PLACEHOLDER)
        form.addRow("ElevenLabs API key:", self._elevenlabs_input)
        layout.addLayout(form)

        self._prefer_direct_cb = QCheckBox("Use direct API (offline mode)")
        self._prefer_direct_cb.setChecked(self._settings.prefer_direct_api)
        self._prefer_direct_cb.setToolTip(
            "When enabled, use ELEVENLABS_API_KEY directly instead of the backend. "
            "Useful when the backend is unreachable. Usage is not tracked."
        )
        layout.addWidget(self._prefer_direct_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._on_accept)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        # ElevenLabs API key: save only if user entered a new key (not placeholder)
        key_text = self._elevenlabs_input.text().strip()
        if key_text and key_text != _PLACEHOLDER:
            self._settings.set_elevenlabs_api_key(key_text)
            self._saved = True
        elif not key_text and self._settings.get_elevenlabs_api_key():
            self._settings.set_elevenlabs_api_key("")
            self._saved = True

        prev = self._settings.prefer_direct_api
        self._settings.prefer_direct_api = self._prefer_direct_cb.isChecked()
        if prev != self._settings.prefer_direct_api:
            self._saved = True
            try:
                from live_dubbing.config.settings import ConfigManager
                ConfigManager().save(self._settings)
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.exception("Failed to save settings (prefer_direct_api)")
                QMessageBox.warning(
                    self,
                    "Settings Not Saved",
                    "Your settings could not be saved. Changes may not persist.",
                )
        self.accept()

    @property
    def was_saved(self) -> bool:
        """Whether the user saved changes."""
        return self._saved
