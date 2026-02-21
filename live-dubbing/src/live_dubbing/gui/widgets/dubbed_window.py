"""
Detachable dubbed/translation display window.

A floating, always-on-top overlay that shows the live translated text.
Supports:
- Adjustable font size
- Adjustable window opacity (background transparency)
- Adjustable text opacity (text visibility)
- Remembers position and size across restarts
- Can be re-docked back into the main window
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from live_dubbing.config.settings import UIConfig

logger = structlog.get_logger(__name__)


class DubbedWindow(QWidget):
    """
    Floating window that displays live translated (dubbed) text.

    Emitted signals:
        reattach_requested: User clicked the dock button to re-attach.
    """

    reattach_requested = pyqtSignal()

    def __init__(
        self,
        ui_config: UIConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ui_config = ui_config

        self._setup_window()
        self._setup_ui()
        self._apply_settings()

    # ── Window setup ──────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        """Configure window flags and restore geometry."""
        self.setWindowTitle("Live Translation")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setMinimumSize(250, 120)

        # Restore position/size
        w = max(250, self._ui_config.dubbed_window_width or 500)
        h = max(120, self._ui_config.dubbed_window_height or 300)
        self.resize(w, h)

        if self._ui_config.dubbed_window_x is not None:
            self.move(
                self._ui_config.dubbed_window_x,
                self._ui_config.dubbed_window_y or 100,
            )

    def _setup_ui(self) -> None:
        """Build the internal layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Toolbar row ───────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        # Font size control
        toolbar.addWidget(QLabel("Size:"))
        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(8, 48)
        self._font_slider.setFixedWidth(80)
        self._font_slider.setToolTip("Adjust text font size")
        self._font_slider.valueChanged.connect(self._on_font_size_changed)
        toolbar.addWidget(self._font_slider)

        self._font_label = QLabel("14")
        self._font_label.setFixedWidth(24)
        toolbar.addWidget(self._font_label)

        toolbar.addSpacing(8)

        # Window opacity control
        toolbar.addWidget(QLabel("Window:"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(20, 100)  # 0.2 – 1.0 mapped to 20–100
        self._opacity_slider.setFixedWidth(80)
        self._opacity_slider.setToolTip("Adjust window background transparency")
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        toolbar.addWidget(self._opacity_slider)

        self._opacity_label = QLabel("100%")
        self._opacity_label.setFixedWidth(36)
        toolbar.addWidget(self._opacity_label)

        toolbar.addSpacing(8)

        # Text opacity control
        toolbar.addWidget(QLabel("Text:"))
        self._text_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._text_opacity_slider.setRange(20, 100)  # 0.2 – 1.0
        self._text_opacity_slider.setFixedWidth(80)
        self._text_opacity_slider.setToolTip("Adjust text visibility")
        self._text_opacity_slider.valueChanged.connect(self._on_text_opacity_changed)
        toolbar.addWidget(self._text_opacity_slider)

        self._text_opacity_label = QLabel("100%")
        self._text_opacity_label.setFixedWidth(36)
        toolbar.addWidget(self._text_opacity_label)

        toolbar.addStretch()

        # Dock button (re-attach)
        self._dock_btn = QPushButton("Dock")
        self._dock_btn.setToolTip("Re-attach this window to the main window")
        self._dock_btn.setFixedWidth(50)
        self._dock_btn.clicked.connect(self._on_dock_clicked)
        toolbar.addWidget(self._dock_btn)

        # Clear button
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Clear all text")
        self._clear_btn.setFixedWidth(50)
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        toolbar.addWidget(self._clear_btn)

        layout.addLayout(toolbar)

        # ── Text display ──────────────────────────────────────────────────
        self._text_display = QTextEdit()
        self._text_display.setReadOnly(True)
        self._text_display.setPlaceholderText(
            "Translated text will appear here..."
        )
        self._text_display.setStyleSheet(
            """
            QTextEdit {
                background-color: #1a1a2e;
                color: #eaeaea;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }
            """
        )
        layout.addWidget(self._text_display, 1)

    def _apply_settings(self) -> None:
        """Apply saved font size, window opacity and text opacity from config."""
        font_size = self._ui_config.dubbed_font_size
        opacity_pct = int(self._ui_config.dubbed_opacity * 100)
        text_opacity_pct = int(self._ui_config.dubbed_text_opacity * 100)

        self._font_slider.setValue(font_size)
        self._opacity_slider.setValue(opacity_pct)
        self._text_opacity_slider.setValue(text_opacity_pct)

        self._update_font(font_size)
        self._update_opacity(opacity_pct)
        self._update_text_opacity(text_opacity_pct)

    # ── Public API ────────────────────────────────────────────────────────

    def append_text(self, text: str) -> None:
        """Append translated text to the display."""
        self._text_display.append(text)
        scrollbar = self._text_display.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def clear_text(self) -> None:
        """Clear all text from the display."""
        self._text_display.clear()

    def get_font_size(self) -> int:
        """Return the current font size."""
        return self._font_slider.value()

    def get_opacity(self) -> float:
        """Return the current window opacity (0.2 – 1.0)."""
        return self._opacity_slider.value() / 100.0

    def get_text_opacity(self) -> float:
        """Return the current text opacity (0.2 – 1.0)."""
        return self._text_opacity_slider.value() / 100.0

    # ── Internal ──────────────────────────────────────────────────────────

    def _update_font(self, size: int) -> None:
        """Update the text display font size."""
        font = QFont()
        font.setPointSize(size)
        self._text_display.setFont(font)
        self._font_label.setText(str(size))

    def _update_opacity(self, pct: int) -> None:
        """Update window opacity. pct is 20–100."""
        self.setWindowOpacity(pct / 100.0)
        self._opacity_label.setText(f"{pct}%")

    def _update_text_opacity(self, pct: int) -> None:
        """Update text colour opacity via stylesheet. pct is 20–100."""
        alpha = int(pct * 2.55)  # 0-255
        self._text_display.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: #1a1a2e;
                color: rgba(234, 234, 234, {alpha});
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }}
            """
        )
        self._text_opacity_label.setText(f"{pct}%")

    def _on_font_size_changed(self, value: int) -> None:
        self._update_font(value)
        self._ui_config.dubbed_font_size = value

    def _on_opacity_changed(self, value: int) -> None:
        self._update_opacity(value)
        self._ui_config.dubbed_opacity = value / 100.0

    def _on_text_opacity_changed(self, value: int) -> None:
        self._update_text_opacity(value)
        self._ui_config.dubbed_text_opacity = value / 100.0

    def _on_dock_clicked(self) -> None:
        self._save_geometry()
        self.reattach_requested.emit()
        self.hide()

    def _on_clear_clicked(self) -> None:
        self._text_display.clear()

    def _save_geometry(self) -> None:
        """Persist position and size to config."""
        self._ui_config.dubbed_window_x = self.x()
        self._ui_config.dubbed_window_y = self.y()
        self._ui_config.dubbed_window_width = self.width()
        self._ui_config.dubbed_window_height = self.height()

    # ── Overrides ─────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Intercept close — treat as dock instead of destroy."""
        self._save_geometry()
        self.reattach_requested.emit()
        if event is not None:
            event.accept()
