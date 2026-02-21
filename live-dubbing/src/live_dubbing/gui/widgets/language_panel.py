"""
Language configuration panel widget.
"""


from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from live_dubbing.gui.languages import (
    get_language_name,
    get_source_languages,
    get_target_languages,
)


class LanguagePanel(QWidget):
    """
    Panel for configuring source and target languages.
    """

    # Signal emitted when language configuration changes
    languages_changed = pyqtSignal(str, str)  # source_code, target_code

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Group box
        group = QGroupBox("Language Configuration")
        group_layout = QVBoxLayout(group)

        # Source language
        source_layout = QHBoxLayout()
        source_label = QLabel("Source Language:")
        source_label.setMinimumWidth(120)
        source_layout.addWidget(source_label)

        self._source_combo = QComboBox()
        for code, name in get_source_languages():
            self._source_combo.addItem(name, code)
        self._source_combo.setCurrentIndex(0)  # Auto-detect
        self._source_combo.currentIndexChanged.connect(self._on_language_changed)
        source_layout.addWidget(self._source_combo, 1)

        group_layout.addLayout(source_layout)

        # Target language
        target_layout = QHBoxLayout()
        target_label = QLabel("Target Language:")
        target_label.setMinimumWidth(120)
        target_layout.addWidget(target_label)

        self._target_combo = QComboBox()
        for code, name in get_target_languages():
            self._target_combo.addItem(name, code)
        self._target_combo.setCurrentIndex(0)  # English
        self._target_combo.currentIndexChanged.connect(self._on_language_changed)
        target_layout.addWidget(self._target_combo, 1)

        group_layout.addLayout(target_layout)

        # Info label
        self._info_label = QLabel(
            "Source language is auto-detected. Select your target language for translation."
        )
        self._info_label.setStyleSheet("color: gray; font-size: 11px;")
        self._info_label.setWordWrap(True)
        group_layout.addWidget(self._info_label)

        layout.addWidget(group)

    def get_source_language(self) -> str:
        """Get selected source language code."""
        return self._source_combo.currentData() or "auto"

    def get_target_language(self) -> str:
        """Get selected target language code."""
        return self._target_combo.currentData() or "en"

    def set_source_language(self, code: str) -> None:
        """Set source language by code."""
        index = self._source_combo.findData(code)
        if index >= 0:
            self._source_combo.setCurrentIndex(index)

    def set_target_language(self, code: str) -> None:
        """Set target language by code."""
        index = self._target_combo.findData(code)
        if index >= 0:
            self._target_combo.setCurrentIndex(index)

    def _on_language_changed(self) -> None:
        """Handle language selection change."""
        source = self.get_source_language()
        target = self.get_target_language()

        # Update info label
        source_name = get_language_name(source)
        target_name = get_language_name(target)

        if source == "auto":
            self._info_label.setText(
                f"Source: Auto-detect -> Target: {target_name}"
            )
        else:
            self._info_label.setText(
                f"Source: {source_name} -> Target: {target_name}"
            )

        self.languages_changed.emit(source, target)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the widget."""
        self._source_combo.setEnabled(enabled)
        self._target_combo.setEnabled(enabled)
