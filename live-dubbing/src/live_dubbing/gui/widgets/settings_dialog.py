"""Settings dialog for API key configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from live_dubbing.config.settings import AppSettings


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
        el_row = QHBoxLayout()
        self._elevenlabs_input = QLineEdit()
        self._elevenlabs_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._elevenlabs_input.setPlaceholderText("Enter your ElevenLabs API key")
        el_row.addWidget(self._elevenlabs_input)
        self._el_toggle = QPushButton("Show")
        self._el_toggle.setFixedWidth(50)
        self._el_toggle.setCheckable(True)
        self._el_toggle.toggled.connect(
            lambda checked: self._toggle_visibility(self._elevenlabs_input, self._el_toggle, checked)
        )
        el_row.addWidget(self._el_toggle)
        api_layout.addLayout(el_row)

        # OpenAI
        api_layout.addWidget(QLabel("OpenAI API Key:"))
        oa_row = QHBoxLayout()
        self._openai_input = QLineEdit()
        self._openai_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_input.setPlaceholderText("Enter your OpenAI API key")
        oa_row.addWidget(self._openai_input)
        self._oa_toggle = QPushButton("Show")
        self._oa_toggle.setFixedWidth(50)
        self._oa_toggle.setCheckable(True)
        self._oa_toggle.toggled.connect(
            lambda checked: self._toggle_visibility(self._openai_input, self._oa_toggle, checked)
        )
        oa_row.addWidget(self._oa_toggle)
        api_layout.addLayout(oa_row)

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

    def _toggle_visibility(
        self, line_edit: QLineEdit, button: QPushButton, show: bool
    ) -> None:
        if show:
            line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            button.setText("Hide")
        else:
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            button.setText("Show")

    def _load_current_keys(self) -> None:
        el_key = self._settings.get_elevenlabs_api_key() or ""
        oa_key = self._settings.get_openai_api_key() or ""
        self._elevenlabs_input.setText(el_key)
        self._openai_input.setText(oa_key)

    def _on_save(self) -> None:
        el_key = self._elevenlabs_input.text().strip()
        oa_key = self._openai_input.text().strip()

        if el_key:
            self._settings.set_elevenlabs_api_key(el_key)
        if oa_key:
            self._settings.set_openai_api_key(oa_key)

        self._saved = True
        self.accept()

    @property
    def was_saved(self) -> bool:
        """Whether the user clicked Save."""
        return self._saved
