"""Overlay bubble uses the app palette and logo mark."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_DUBBED = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "live_dubbing"
    / "gui"
    / "widgets"
    / "dubbed_window.py"
)
_LOGO = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "live_dubbing"
    / "gui"
    / "assets"
    / "logo.png"
)


def test_overlay_source_uses_theme_and_logo() -> None:
    src = _DUBBED.read_text(encoding="utf-8")
    assert "from live_dubbing.gui.theme import" in src
    assert "logo.png" in src
    assert "drawPixmap" in src
    assert "_CYAN" not in src
    assert "gold bubble" not in src.lower()
    assert _LOGO.is_file()


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_bubble_loads_logo_and_paints(qapp, tmp_path: Path) -> None:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage, QPainter

    from live_dubbing.gui.theme import ACCENT
    from live_dubbing.gui.widgets.dubbed_window import _BubbleButton, _LOGO_PATH

    assert _LOGO_PATH.is_file()
    bubble = _BubbleButton()
    assert not bubble._logo.isNull()

    image = QImage(bubble.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    bubble.render(painter)
    painter.end()

    out = tmp_path / "bubble.png"
    assert image.save(str(out))
    assert out.stat().st_size > 0

    accent = tuple(int(ACCENT.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    found_accent = False
    opaque = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 32:
                continue
            opaque += 1
            dist = (
                abs(pixel.red() - accent[0])
                + abs(pixel.green() - accent[1])
                + abs(pixel.blue() - accent[2])
            )
            if dist < 80:
                found_accent = True
                break
        if found_accent:
            break

    assert opaque > 100
    assert found_accent
