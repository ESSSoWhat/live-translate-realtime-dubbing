"""Settings dialog for API key configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from live_dubbing.config.settings import AppSettings

# Shown when a key is already configured; actual key is never displayed.
API_KEY_PLACEHOLDER = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"


class SettingsDialog(QDialog):
    """Dialog for configuring API keys and other settings."""

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._saved = False

        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setModal(True)
        self._setup_ui()
        self._load_current_keys()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # --- API Keys group ---
        api_group = QGroupBox("API Keys")
        api_layout = QVBoxLayout(api_group)

        # ElevenLabs
        api_layout.addWidget(QLabel("ElevenLabs API Key:"))
        self._elevenlabs_input = QLineEdit()
        self._elevenlabs_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._elevenlabs_input.setPlaceholderText("Enter your ElevenLabs API key")
        api_layout.addWidget(self._elevenlabs_input)

        # OpenAI
        api_layout.addWidget(QLabel("OpenAI API Key:"))
        self._openai_input = QLineEdit()
        self._openai_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_input.setPlaceholderText("Enter your OpenAI API key")
        api_layout.addWidget(self._openai_input)

        info_label = QLabel(
            "Keys are stored securely in Windows Credential Manager."
        )
        info_label.setStyleSheet("color: gray; font-size: 11px;")
        api_layout.addWidget(info_label)

        layout.addWidget(api_group)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_current_keys(self) -> None:
        """Load state: show placeholder when a key exists so the key is never visible."""
        el_key = self._settings.get_elevenlabs_api_key()
        oa_key = self._settings.get_openai_api_key()
        self._elevenlabs_input.setText(API_KEY_PLACEHOLDER if el_key else "")
        self._openai_input.setText(API_KEY_PLACEHOLDER if oa_key else "")

    def _on_save(self) -> None:
        el_key = self._elevenlabs_input.text().strip()
        oa_key = self._openai_input.text().strip()
        # Only update when user changed the value; placeholder means keep existing
        if el_key != API_KEY_PLACEHOLDER:
            self._settings.set_elevenlabs_api_key(el_key)
        if oa_key != API_KEY_PLACEHOLDER:
            self._settings.set_openai_api_key(oa_key)

        self._saved = True
        self.accept()

    @property
    def was_saved(self) -> bool:
        """Whether the user clicked Save."""
        return self._saved
