"""Live Translate desktop theme, matched to the live site and Dubtool.

Colors are the livetranslate.net theme tokens: indigo surfaces and the gold action color.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

# Site tokens: background, panel, line, muted, text, action, button ink.
BG = "#151723"
PANEL = "#1B1D2B"
FIELD = "#10121C"
LINE = "#343749"
TEXT = "#9195BB"
MUTED = "#7D809C"
ACCENT = "#FCA311"
ACCENT_HOVER = "#FFC14D"
ACCENT_INK = "#14213D"
OK = "#97C693"
WARN = "#E2C16A"
DANGER = "#9C2426"
DANGER_HOVER = "#C43A3C"
GLOW = "#2A3150"

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
QWidget#AppRoot, QWidget#ScrollContent {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QLabel#muted {{
    color: {MUTED};
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
    background: {ACCENT_HOVER};
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
QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QComboBox, QSpinBox {{
    background: {FIELD};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: rgba(252, 163, 17, 0.28);
    selection-color: {ACCENT_INK};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    selection-background-color: rgba(252, 163, 17, 0.28);
    selection-color: {ACCENT_INK};
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-bottom: 1px solid {LINE};
}}
QListWidget::item:selected {{
    background: rgba(252, 163, 17, 0.22);
    color: {TEXT};
}}
QListWidget::item:hover {{
    background: rgba(252, 163, 17, 0.10);
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
    background: rgba(252, 163, 17, 0.22);
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


def button_css(fill: str, ink: str, hover: str, *, size: str = "") -> str:
    """A solid button in the site palette. White text fails on the gold action color."""
    size_rule = f"font-size: {size};" if size else ""
    return (
        "QPushButton {"
        f"background-color: {fill}; color: {ink}; font-weight: 600;"
        f"border: none; border-radius: 8px; padding: 4px 12px; {size_rule}"
        "}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:disabled {{ background-color: {LINE}; color: {MUTED}; }}"
    )


def progress_css(chunk: str) -> str:
    """Progress bar on the site field color."""
    return (
        "QProgressBar {"
        f"border: 1px solid {LINE}; border-radius: 6px; text-align: center;"
        f"background: {FIELD}; color: {TEXT};"
        "}"
        f"QProgressBar::chunk {{ background-color: {chunk}; border-radius: 5px; }}"
    )


def apply_theme(app: QApplication) -> None:
    """Apply the site palette to the whole desktop app."""
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
