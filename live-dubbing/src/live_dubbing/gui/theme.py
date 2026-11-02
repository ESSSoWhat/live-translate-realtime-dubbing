"""Live Translate desktop theme, matched to the DubTool brand."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication

BG = "#04080f"
PANEL = "#0b1c30"
LINE = "#1c5878"
TEXT = "#e7f6ff"
MUTED = "#8eb8cc"
ACCENT = "#3fd4ff"
ACCENT_INK = "#04202c"
OK = "#7ddec8"
WARN = "#e2c16a"
FIELD = "#071421"
GLOW = "#0e4d73"
DANGER = "#ff8b8b"

_LOGO = Path(__file__).resolve().parent / "assets" / "logo.png"

APP_STYLESHEET = f"""
QMainWindow, QDialog {{
    background: qradialgradient(
        cx:0.5, cy:0, radius:1.05, fx:0.5, fy:0,
        stop:0 {GLOW}, stop:0.46 {BG}
    );
    color: {TEXT};
    font-family: "Segoe UI";
}}
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI";
}}
QWidget#AppRoot, QWidget#ScrollContent, QWidget#brandHeader {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QLabel#wordmark {{
    font-size: 26px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#tagline, QLabel#muted {{
    color: {MUTED};
}}
QLabel#accountLine {{
    color: {TEXT};
    font-size: 13px;
}}
QGroupBox {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 14px;
    margin-top: 16px;
    padding: 18px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 0 6px;
    color: {TEXT};
}}
QPushButton {{
    background: {ACCENT};
    color: {ACCENT_INK};
    border: none;
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #68e4ff;
}}
QPushButton:disabled {{
    background: {LINE};
    color: {MUTED};
}}
QPushButton#secondary {{
    background: transparent;
    color: {TEXT};
    border: 1px solid {LINE};
}}
QPushButton#secondary:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
    background: transparent;
}}
QPushButton#secondary:disabled {{
    background: transparent;
    color: {MUTED};
    border-color: {LINE};
}}
QPushButton#link {{
    background: transparent;
    color: {ACCENT};
    border: none;
    font-weight: 500;
    padding: 2px 4px;
}}
QPushButton#link:hover {{
    color: {TEXT};
    background: transparent;
}}
QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QComboBox, QSpinBox {{
    background: {FIELD};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: rgba(63, 212, 255, 0.28);
    selection-color: {TEXT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    selection-background-color: rgba(63, 212, 255, 0.28);
    selection-color: {TEXT};
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-bottom: 1px solid {LINE};
}}
QListWidget::item:selected {{
    background: rgba(63, 212, 255, 0.18);
    color: {TEXT};
}}
QListWidget::item:hover {{
    background: rgba(63, 212, 255, 0.08);
}}
QCheckBox {{
    color: {TEXT};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {LINE};
    border-radius: 3px;
    background: {FIELD};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QProgressBar {{
    background: {FIELD};
    border: 1px solid {LINE};
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {LINE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QMenuBar {{
    background: transparent;
    color: {MUTED};
}}
QMenuBar::item:selected {{
    color: {ACCENT};
    background: transparent;
}}
QMenu {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    padding: 4px;
}}
QMenu::item:selected {{
    background: rgba(63, 212, 255, 0.18);
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: {LINE};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QToolTip {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
}}
"""


def brand_mark(size: int) -> QPixmap:
    """Square globe mark with the wordmark cropped off."""
    pix = QPixmap(str(_LOGO))
    if pix.isNull() or size <= 0:
        return QPixmap()
    crop_h = max(1, int(pix.height() * 0.72))
    cropped = pix.copy(0, 0, pix.width(), crop_h)
    side = min(cropped.width(), cropped.height())
    x = (cropped.width() - side) // 2
    y = (cropped.height() - side) // 2
    square = cropped.copy(x, y, side, side)
    return square.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )


def apply_theme(app: QApplication) -> None:
    """Apply the DubTool palette and stylesheet to the whole desktop app."""
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(FIELD))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(ACCENT_INK))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(ACCENT_INK))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)
