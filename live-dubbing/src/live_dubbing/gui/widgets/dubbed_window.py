"""
Floating always-on-top Live Translate overlay HUD.

Collapsed: the Live Translate globe mark. Expanded: captions plus session
controls (status, voice, volume, mute, clone, stop, home).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextCharFormat,
    QTextCursor,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
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

_CYAN = "#68F8F8"
_CYAN_SOFT = "#3CF0F0"
_NAVY = "#000810"
_NAVY_SURFACE = "#001018"
_NAVY_CARD = "#001830"
_ON_SURFACE = "#E8F7FF"
_MUTED = "#8AA4B5"
_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo.png"

_COLLAPSED = 96
_EXPANDED_WIDTH = 320
_EXPANDED_HEIGHT = 484
_DRAG_THRESHOLD = 6

_brand_square: QPixmap | None = None


def _brand_square_pixmap() -> QPixmap:
    """Return a cached square crop of the globe mark (wordmark cropped off)."""
    global _brand_square
    if _brand_square is not None:
        return _brand_square
    pix = QPixmap(str(_LOGO_PATH))
    if pix.isNull():
        _brand_square = QPixmap()
        return _brand_square
    crop_h = max(1, int(pix.height() * 0.72))
    cropped = pix.copy(0, 0, pix.width(), crop_h)
    side = min(cropped.width(), cropped.height())
    x = (cropped.width() - side) // 2
    y = (cropped.height() - side) // 2
    _brand_square = cropped.copy(x, y, side, side)
    return _brand_square


def _globe_pixmap(size: int) -> QPixmap:
    """Draw the Live Translate globe mark when logo.png is missing."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = max(2, size // 14)
    rect = pix.rect().adjusted(margin, margin, -margin, -margin)
    painter.setPen(QPen(QColor(_CYAN), max(2.0, size / 22.0)))
    painter.setBrush(QColor("#001A28"))
    painter.drawEllipse(rect)
    clip = QPainterPath()
    clip.addEllipse(QRectF(rect))
    painter.setClipPath(clip)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    cx = rect.center().x()
    for scale in (0.42, 0.72, 1.0):
        width = max(4, int(rect.width() * scale))
        mer = QRect(cx - width // 2, rect.top(), width, rect.height())
        painter.drawEllipse(mer)
    for t in (0.32, 0.5, 0.68):
        y = rect.top() + int(rect.height() * t)
        painter.drawLine(rect.left() + 1, y, rect.right() - 1, y)
    painter.end()
    return pix


def _scaled_brand_mark(size: int) -> QPixmap:
    """Scale the brand mark to ``size``×``size`` for labels and the bubble."""
    if size <= 0:
        return QPixmap()
    src = _brand_square_pixmap()
    if src.isNull():
        return _globe_pixmap(size)
    return src.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )


class _BubbleButton(QWidget):
    """Circular brand-mark bubble painted without native button chrome."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._muted = False
        self.setFixedSize(_COLLAPSED, _COLLAPSED)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move · Click to expand")

    def set_muted(self, muted: bool) -> None:
        """Dim the mark when TTS is muted."""
        self._muted = muted
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: ARG002
        """Draw a neon cyan ring around the Live Translate globe mark."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 1-alpha fill so the full square receives mouse events (layered windows
        # ignore fully transparent pixels).
        painter.fillRect(self.rect(), QColor(0, 8, 16, 1))

        center = QPointF(self.rect().center())
        glow = QRadialGradient(center, self.width() / 2.0)
        glow.setColorAt(0.52, QColor(104, 248, 248, 0))
        glow.setColorAt(0.78, QColor(104, 248, 248, 80))
        glow.setColorAt(1.0, QColor(104, 248, 248, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(self.rect())

        margin = 8
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.setBrush(QColor(_NAVY))
        painter.drawEllipse(rect)

        clip = QPainterPath()
        clip.addEllipse(QRectF(rect))
        painter.save()
        painter.setClipPath(clip)
        mark = _scaled_brand_mark(max(rect.width(), rect.height()))
        if mark.isNull():
            painter.setPen(QColor(_CYAN))
            font = QFont("Segoe UI", 11, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "LT")
        else:
            x = rect.x() + (rect.width() - mark.width()) // 2
            y = rect.y() + (rect.height() - mark.height()) // 2
            painter.drawPixmap(x, y, mark)
        painter.restore()

        if self._muted:
            painter.setBrush(QColor(0, 8, 16, 150))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor(_CYAN), 3))
            inset = rect.adjusted(18, 18, -18, -18)
            painter.drawLine(inset.topLeft(), inset.bottomRight())

        ring = QPen(QColor(_CYAN))
        ring.setWidth(2)
        painter.setPen(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect.adjusted(1, 1, -1, -1))


def _split_chunks(text: str) -> list[str]:
    """Split pasted or streamed caption text into utterance chunks."""
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    return [ln for ln in lines if ln]


class _CaptionPane(QWidget):
    """Caption history with a slider that highlights the active utterance."""

    def __init__(
        self,
        title: str,
        *,
        bold: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._chunks: list[str] = []
        self._spans: list[tuple[int, int]] = []
        self._index = 0
        self._follow = True
        self._live = False
        self._bold = bold
        self._font_size = 14
        self._text_alpha = 255

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        layout.addWidget(title_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlaceholderText(f"{title} captions appear here.")
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.installEventFilter(self)
        row.addWidget(self._text, 1)

        self._slider = QSlider(Qt.Orientation.Vertical)
        self._slider.setRange(0, 0)
        self._slider.setEnabled(False)
        self._slider.setInvertedAppearance(True)
        self._slider.setInvertedControls(True)
        self._slider.setFixedWidth(18)
        self._slider.setToolTip(
            "Scroll captions — highlighted text is the current utterance"
        )
        self._slider.valueChanged.connect(self._on_slider_changed)
        row.addWidget(self._slider)
        layout.addLayout(row, 1)
        self._apply_text_style()

    def append_chunk(self, text: str) -> None:
        """Add an utterance and highlight it as the current line."""
        for chunk in _split_chunks(text):
            self._chunks.append(chunk)
        if not self._chunks:
            return
        self._index = len(self._chunks) - 1
        self._follow = True
        self._refresh(scroll_to_index=True)

    def highlight_matching(self, text: str, *, live: bool = False) -> None:
        """Highlight the chunk currently being captured / translated / spoken."""
        needle = (text or "").strip()
        if not self._chunks:
            return
        index = len(self._chunks) - 1
        if needle:
            for i in range(len(self._chunks) - 1, -1, -1):
                chunk = self._chunks[i]
                if chunk == needle or needle in chunk or chunk in needle:
                    index = i
                    break
        self._index = index
        self._follow = index == len(self._chunks) - 1
        self._live = live
        self._refresh(scroll_to_index=True)

    def set_live(self, live: bool) -> None:
        """Toggle stronger highlight for the active pipeline section."""
        if self._live == live:
            return
        self._live = live
        self._apply_highlight()

    def clear(self) -> None:
        """Remove all caption history."""
        self._chunks.clear()
        self._spans.clear()
        self._index = 0
        self._follow = True
        self._live = False
        self._text.clear()
        self._slider.blockSignals(True)
        self._slider.setRange(0, 0)
        self._slider.setEnabled(False)
        self._slider.blockSignals(False)
        self._text.setExtraSelections([])

    def set_font_size(self, size: int, *, bold: bool | None = None) -> None:
        """Update caption font size."""
        self._font_size = size
        if bold is not None:
            self._bold = bold
        font = QFont()
        font.setPointSize(size)
        font.setBold(self._bold)
        self._text.setFont(font)

    def set_text_alpha(self, alpha: int) -> None:
        """Update caption text alpha (0–255)."""
        self._text_alpha = max(0, min(255, alpha))
        self._apply_text_style()

    def _apply_text_style(self) -> None:
        color = f"rgba(232, 247, 255, {self._text_alpha})"
        self._text.setStyleSheet(
            f"background-color: {_NAVY}; color: {color}; "
            f"border: 1px solid #003050; border-radius: 6px; padding: 6px;"
        )

    def _on_slider_changed(self, value: int) -> None:
        if not self._chunks:
            return
        self._index = max(0, min(value, len(self._chunks) - 1))
        self._follow = self._index >= len(self._chunks) - 1
        self._apply_highlight()

    def _refresh(self, *, scroll_to_index: bool) -> None:
        last = max(0, len(self._chunks) - 1)
        self._slider.blockSignals(True)
        self._slider.setRange(0, last)
        self._slider.setEnabled(len(self._chunks) > 1)
        if scroll_to_index:
            self._slider.setValue(self._index)
        self._slider.blockSignals(False)
        self._rebuild_document()
        self._apply_highlight()

    def _rebuild_document(self) -> None:
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        pos = 0
        for i, chunk in enumerate(self._chunks):
            if i:
                parts.append("\n")
                pos += 1
            start = pos
            parts.append(chunk)
            pos += len(chunk)
            spans.append((start, pos))
        self._spans = spans
        self._text.setPlainText("".join(parts))

    def _apply_highlight(self) -> None:
        if not self._spans or not (0 <= self._index < len(self._spans)):
            self._text.setExtraSelections([])
            return
        start, end = self._spans[self._index]
        cursor = self._text.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        if self._live:
            fmt.setBackground(QColor("#0E6A8F"))
            fmt.setForeground(QColor("#B8FFFF"))
        else:
            fmt.setBackground(QColor("#0A4A68"))
            fmt.setForeground(QColor(_CYAN))
        selection = QTextEdit.ExtraSelection()
        selection.format = fmt
        selection.cursor = cursor
        self._text.setExtraSelections([selection])
        show = self._text.textCursor()
        show.setPosition(start)
        self._text.setTextCursor(show)
        self._text.ensureCursorVisible()

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        """Map mouse-wheel on the caption pane to the utterance slider."""
        if obj is self._text and isinstance(event, QWheelEvent) and self._chunks:
            delta = event.angleDelta().y()
            if delta == 0:
                return False
            step = -1 if delta > 0 else 1
            new_val = max(0, min(self._slider.maximum(), self._slider.value() + step))
            self._slider.setValue(new_val)
            return True
        return super().eventFilter(obj, event)


class DubbedWindow(QWidget):
    """
    Compact always-on-top overlay for a live translation session.

    Emitted signals:
        reattach_requested: Overlay closed while idle (legacy dock).
        stop_requested: User tapped Stop.
        mute_toggled: Mute button toggled (payload is new muted state).
        volume_changed: Volume slider released (0.0–1.0).
        voice_selected: Voice combo changed (voice id).
        clone_requested: User tapped Clone.
        home_requested: User tapped Home (show main window, keep session).
    """

    reattach_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    mute_toggled = pyqtSignal(bool)
    volume_changed = pyqtSignal(float)
    voice_selected = pyqtSignal(str)
    clone_requested = pyqtSignal()
    home_requested = pyqtSignal()

    def __init__(
        self,
        ui_config: UIConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ui_config = ui_config
        self._expanded = False
        self._muted = False
        self._cloning = False
        self._session_active = False
        self._font_size = ui_config.dubbed_font_size
        self._window_opacity = ui_config.dubbed_opacity
        self._text_opacity = ui_config.dubbed_text_opacity
        self._press_global: QPoint | None = None
        self._press_window = QPoint()
        self._dragging = False
        self._press_on_bubble = False
        self._updating_voice = False

        self._setup_window()
        self._setup_ui()
        self._apply_settings()
        self._set_expanded(False, save_corner=False)

    # ── Window setup ──────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        """Configure frameless always-on-top tool window."""
        self.setWindowTitle("Live Translate")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

        if self._ui_config.dubbed_window_x is not None:
            self.move(
                self._ui_config.dubbed_window_x,
                self._ui_config.dubbed_window_y or 100,
            )
        else:
            self._move_to_default_position()

    def _move_to_default_position(self) -> None:
        """Place the collapsed bubble on the right edge of the primary screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - _COLLAPSED - 16, geo.center().y() - _COLLAPSED // 2)

    def _setup_ui(self) -> None:
        """Build collapsed bubble and expanded panel."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._bubble = _BubbleButton(self)
        root.addWidget(self._bubble, alignment=Qt.AlignmentFlag.AlignTop)

        self._panel = QWidget(self)
        self._panel.setObjectName("overlayPanel")
        self._panel.setStyleSheet(
            f"""
            QWidget#overlayPanel {{
                background-color: {_NAVY_SURFACE};
                border: 1px solid {_CYAN_SOFT};
                border-radius: 18px;
            }}
            QLabel {{
                color: {_ON_SURFACE};
                font-family: "Segoe UI";
            }}
            QComboBox {{
                background-color: {_NAVY_CARD};
                color: {_ON_SURFACE};
                border: 1px solid #0A4A5A;
                border-radius: 8px;
                padding: 4px 8px;
            }}
            QTextEdit {{
                background-color: {_NAVY};
                color: {_ON_SURFACE};
                border: 1px solid #0A4A5A;
                border-radius: 10px;
                padding: 6px;
            }}
            QPushButton {{
                background-color: {_NAVY_CARD};
                color: {_ON_SURFACE};
                border: 1px solid #0A4A5A;
                border-radius: 8px;
                padding: 6px 10px;
            }}
            QPushButton:hover {{
                background-color: #00283C;
                border-color: {_CYAN};
            }}
            QPushButton:disabled {{
                color: {_MUTED};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: #002040;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {_CYAN};
                width: 12px;
                margin: -5px 0;
                border-radius: 6px;
            }}
            QSlider::groove:vertical {{
                width: 4px;
                background: #002040;
                border-radius: 2px;
            }}
            QSlider::handle:vertical {{
                background: {_CYAN};
                height: 12px;
                margin: 0 -5px;
                border-radius: 6px;
            }}
            """
        )
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(12, 10, 10, 10)
        panel_layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        logo_label = QLabel()
        logo_label.setFixedSize(28, 28)
        mark = _scaled_brand_mark(28)
        if not mark.isNull():
            logo_label.setPixmap(mark)
            logo_label.setScaledContents(True)
        brand.addWidget(logo_label)
        wordmark = QLabel("LIVE TRANSLATE")
        wordmark.setStyleSheet(
            f"color: {_CYAN}; font-weight: 800; font-size: 12px; letter-spacing: 1px;"
        )
        brand.addWidget(wordmark, 1)
        panel_layout.addLayout(brand)

        header = QHBoxLayout()
        header.setSpacing(4)
        self._status_label = QLabel("Listening…")
        self._status_label.setStyleSheet(f"font-weight: 600; color: {_ON_SURFACE};")
        header.addWidget(self._status_label, 1)

        self._collapse_btn = QPushButton("–")
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.setToolTip("Collapse")
        self._collapse_btn.clicked.connect(lambda: self._set_expanded(False))
        header.addWidget(self._collapse_btn)

        self._home_btn = QPushButton("Home")
        self._home_btn.setFixedWidth(52)
        self._home_btn.setToolTip("Show the main window without stopping")
        self._home_btn.clicked.connect(self.home_requested.emit)
        header.addWidget(self._home_btn)
        panel_layout.addLayout(header)

        self._voice_combo = QComboBox()
        self._voice_combo.setToolTip("TTS voice")
        self._voice_combo.currentIndexChanged.connect(self._on_voice_index_changed)
        panel_layout.addWidget(self._voice_combo)

        vol_row = QHBoxLayout()
        self._vol_icon = QLabel("Vol")
        self._vol_icon.setFixedWidth(28)
        vol_row.addWidget(self._vol_icon)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setToolTip("TTS output volume")
        self._volume_slider.valueChanged.connect(self._on_volume_slider_moved)
        self._volume_slider.sliderReleased.connect(self._on_volume_slider_released)
        vol_row.addWidget(self._volume_slider, 1)
        self._volume_label = QLabel("100%")
        self._volume_label.setFixedWidth(36)
        self._volume_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        vol_row.addWidget(self._volume_label)
        panel_layout.addLayout(vol_row)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(20, 100)
        self._opacity_slider.setValue(int(self._window_opacity * 100))
        self._opacity_slider.setToolTip("Overlay window opacity")
        self._opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        opacity_row.addWidget(self._opacity_slider, 1)
        self._opacity_label = QLabel(f"{int(self._window_opacity * 100)}%")
        self._opacity_label.setFixedWidth(36)
        self._opacity_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        opacity_row.addWidget(self._opacity_label)
        panel_layout.addLayout(opacity_row)

        self._source_pane = _CaptionPane("Source", parent=self._panel)
        panel_layout.addWidget(self._source_pane, 1)

        self._translated_pane = _CaptionPane(
            "Translation", bold=True, parent=self._panel
        )
        panel_layout.addWidget(self._translated_pane, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self._clone_btn = QPushButton("Clone")
        self._clone_btn.setToolTip("Clone the current speaker from live audio")
        self._clone_btn.clicked.connect(self._on_clone_clicked)
        actions.addWidget(self._clone_btn, 1)

        self._mute_btn = QPushButton("Mute")
        self._mute_btn.setToolTip("Mute or unmute TTS")
        self._mute_btn.clicked.connect(self._on_mute_clicked)
        actions.addWidget(self._mute_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop translation and restore the main window")
        self._stop_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {_CYAN};
                color: {_NAVY};
                font-weight: bold;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: #9AFFFF; }}
            """
        )
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        actions.addWidget(self._stop_btn)
        panel_layout.addLayout(actions)

        root.addWidget(self._panel)
        self._install_drag_filters()

    def _install_drag_filters(self) -> None:
        """Let chrome (not sliders/buttons/captions) drag the overlay."""
        self._panel.setCursor(Qt.CursorShape.OpenHandCursor)
        self._bubble.installEventFilter(self)
        self._panel.installEventFilter(self)
        self._source_pane.installEventFilter(self)
        self._translated_pane.installEventFilter(self)
        for label in self.findChildren(QLabel):
            if not self._widget_is_interactive(label):
                label.installEventFilter(self)

    def _widget_is_interactive(self, widget: QWidget | None) -> bool:
        """True for controls that must keep their own mouse handling."""
        current = widget
        while current is not None and current is not self:
            if isinstance(current, (QPushButton, QSlider, QComboBox, QTextEdit)):
                return True
            current = current.parentWidget()
        return False

    def _apply_settings(self) -> None:
        """Apply saved font size and opacities from config."""
        self._update_font(self._font_size)
        self._update_text_opacity(self._text_opacity)
        pct = int(self._window_opacity * 100)
        self._opacity_slider.blockSignals(True)
        self._opacity_slider.setValue(pct)
        self._opacity_slider.blockSignals(False)
        self._on_opacity_slider_changed(pct)

    # ── Public API ────────────────────────────────────────────────────────

    def append_text(self, text: str) -> None:
        """Append translated text and highlight it as the active translation."""
        self._translated_pane.append_chunk(text)
        self._translated_pane.set_live(True)
        self._source_pane.set_live(False)

    def append_source_text(self, text: str) -> None:
        """Append source transcription and highlight it as the active capture."""
        self._source_pane.append_chunk(text)
        self._source_pane.set_live(True)

    def highlight_spoken_text(self, text: str) -> None:
        """Highlight the translation chunk currently being spoken."""
        self._translated_pane.highlight_matching(text, live=True)
        self._source_pane.set_live(False)

    def highlight_source_text(self, text: str) -> None:
        """Highlight the source chunk currently being captured / translated."""
        self._source_pane.highlight_matching(text, live=True)

    def clear_text(self) -> None:
        """Clear source and translation captions."""
        self._source_pane.clear()
        self._translated_pane.clear()

    def get_font_size(self) -> int:
        """Return the current caption font size."""
        return self._font_size

    def get_opacity(self) -> float:
        """Return the current window opacity (0.2 – 1.0)."""
        return self._window_opacity

    def get_text_opacity(self) -> float:
        """Return the current text opacity (0.2 – 1.0)."""
        return self._text_opacity

    def set_font_size(self, size: int) -> None:
        """Update caption font size and persist it."""
        self._font_size = max(8, min(48, size))
        self._ui_config.dubbed_font_size = self._font_size
        self._update_font(self._font_size)

    def set_text_opacity(self, opacity: float) -> None:
        """Update caption text opacity (0.2 – 1.0) and persist it."""
        self._text_opacity = max(0.2, min(1.0, opacity))
        self._ui_config.dubbed_text_opacity = self._text_opacity
        self._update_text_opacity(self._text_opacity)

    def set_opacity(self, opacity: float) -> None:
        """Update overlay window opacity (0.2 – 1.0) and persist it."""
        pct = int(max(0.2, min(1.0, opacity)) * 100)
        self._opacity_slider.blockSignals(True)
        self._opacity_slider.setValue(pct)
        self._opacity_slider.blockSignals(False)
        self._on_opacity_slider_changed(pct)

    def set_status(self, text: str) -> None:
        """Update the status line in the expanded panel."""
        self._status_label.setText(text or "Listening…")

    def set_muted(self, muted: bool) -> None:
        """Sync mute UI without emitting mute_toggled."""
        self._muted = muted
        self._mute_btn.setText("Unmute" if muted else "Mute")
        self._bubble.set_muted(muted)
        self._volume_slider.setEnabled(not muted)

    def set_volume(self, volume: float) -> None:
        """Sync volume slider without emitting volume_changed. volume is 0.0–1.0."""
        pct = int(max(0.0, min(1.0, volume)) * 100)
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(pct)
        self._volume_slider.blockSignals(False)
        self._volume_label.setText(f"{pct}%")

    def set_voices(
        self,
        voices: list[tuple[str, str]],
        selected_id: str | None = None,
    ) -> None:
        """Populate the voice combo. Each item is (voice_id, name)."""
        self._updating_voice = True
        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        select_index = 0
        for i, (voice_id, name) in enumerate(voices):
            self._voice_combo.addItem(name or voice_id, voice_id)
            if selected_id and voice_id == selected_id:
                select_index = i
        if self._voice_combo.count() == 0:
            self._voice_combo.addItem("No voices yet", "")
        self._voice_combo.setCurrentIndex(select_index)
        self._voice_combo.blockSignals(False)
        self._updating_voice = False

    def set_cloning(self, cloning: bool) -> None:
        """Disable clone/stop-adjacent controls while a clone is in progress."""
        self._cloning = cloning
        self._clone_btn.setText("Cloning…" if cloning else "Clone")
        self._clone_btn.setEnabled(not cloning)
        self._mute_btn.setEnabled(not cloning)
        if cloning:
            self.set_status("Cloning voice…")

    def set_session_active(self, active: bool) -> None:
        """Track whether a live session is running (affects close behavior)."""
        self._session_active = active

    def expand(self) -> None:
        """Show the expanded control panel."""
        self._set_expanded(True)

    def collapse(self) -> None:
        """Show the collapsed bubble."""
        self._set_expanded(False)

    # ── Internal ──────────────────────────────────────────────────────────

    def _update_font(self, size: int) -> None:
        self._source_pane.set_font_size(size, bold=False)
        self._translated_pane.set_font_size(size, bold=True)

    def _update_window_opacity(self, opacity: float) -> None:
        # Collapsed bubble stays fully opaque so the globe mark stays visible.
        if not self._expanded:
            self.setWindowOpacity(1.0)
            return
        self.setWindowOpacity(max(0.2, min(1.0, opacity)))

    def _update_text_opacity(self, opacity: float) -> None:
        alpha = int(max(0.2, min(1.0, opacity)) * 255)
        self._source_pane.set_text_alpha(alpha)
        self._translated_pane.set_text_alpha(alpha)

    def _on_opacity_slider_changed(self, value: int) -> None:
        opacity = value / 100.0
        self._window_opacity = opacity
        self._ui_config.dubbed_opacity = opacity
        self._opacity_label.setText(f"{value}%")
        self._update_window_opacity(opacity)

    def _set_expanded(self, expanded: bool, save_corner: bool = True) -> None:
        """Toggle collapsed bubble vs expanded panel, keeping the right edge."""
        old = self.geometry()
        self._expanded = expanded
        self._bubble.setVisible(not expanded)
        self._panel.setVisible(expanded)
        if expanded:
            size = QSize(_EXPANDED_WIDTH, _EXPANDED_HEIGHT)
        else:
            size = QSize(_COLLAPSED, _COLLAPSED)
        self.setFixedSize(size)
        self._update_window_opacity(self._window_opacity)
        if save_corner:
            new_x = old.x() + old.width() - size.width()
            new_y = old.y()
            self._clamp_and_move(new_x, new_y)
        self._save_geometry()

    def _clamp_and_move(self, x: int, y: int) -> None:
        """Keep the overlay on the available screen."""
        screen = QApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left(), min(x, geo.right() - self.width()))
            y = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(x, y)

    def _on_volume_slider_moved(self, value: int) -> None:
        self._volume_label.setText(f"{value}%")

    def _on_volume_slider_released(self) -> None:
        self.volume_changed.emit(self._volume_slider.value() / 100.0)

    def _on_mute_clicked(self) -> None:
        self.set_muted(not self._muted)
        self.mute_toggled.emit(self._muted)

    def _on_clone_clicked(self) -> None:
        if self._cloning:
            return
        self.clone_requested.emit()

    def _on_voice_index_changed(self, index: int) -> None:
        if self._updating_voice or index < 0:
            return
        voice_id = self._voice_combo.itemData(index)
        if isinstance(voice_id, str) and voice_id:
            self.voice_selected.emit(voice_id)

    def _save_geometry(self) -> None:
        """Persist position (and expanded size) to config."""
        self._ui_config.dubbed_window_x = self.x()
        self._ui_config.dubbed_window_y = self.y()
        if self._expanded:
            self._ui_config.dubbed_window_width = self.width()
            self._ui_config.dubbed_window_height = self.height()

    def _begin_drag(self, global_pos: QPoint, *, from_bubble: bool = False) -> None:
        self._press_global = global_pos
        self._press_window = self.pos()
        self._dragging = False
        self._press_on_bubble = from_bubble
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.grabMouse()

    def _update_drag(self, global_pos: QPoint) -> None:
        if self._press_global is None:
            return
        delta = global_pos - self._press_global
        if not self._dragging and delta.manhattanLength() > _DRAG_THRESHOLD:
            self._dragging = True
        if self._dragging:
            self._clamp_and_move(
                self._press_window.x() + delta.x(),
                self._press_window.y() + delta.y(),
            )

    def _end_drag(self) -> bool:
        """Finish a drag. Returns True if this was a click (not a drag)."""
        if self._press_global is None:
            return False
        was_click = not self._dragging
        if self._dragging:
            self._save_geometry()
        self._press_global = None
        self._dragging = False
        self._press_on_bubble = False
        self.unsetCursor()
        if self.mouseGrabber() is self:
            self.releaseMouse()
        if self._expanded:
            self._panel.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self._bubble.setCursor(Qt.CursorShape.OpenHandCursor)
        return was_click

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        """Drag the overlay from chrome; keep clicks on controls working."""
        if event is None or not isinstance(event, QMouseEvent):
            return super().eventFilter(obj, event)
        widget = obj if isinstance(obj, QWidget) else None
        if self._widget_is_interactive(widget) and self._press_global is None:
            return super().eventFilter(obj, event)

        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._begin_drag(
                    event.globalPosition().toPoint(),
                    from_bubble=obj is self._bubble,
                )
                return True
            return False
        if et == QEvent.Type.MouseMove and self._press_global is not None:
            self._update_drag(event.globalPosition().toPoint())
            return True
        if et == QEvent.Type.MouseButtonRelease and self._press_global is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                from_bubble = self._press_on_bubble
                was_click = self._end_drag()
                if was_click and from_bubble and not self._expanded:
                    self._set_expanded(True)
                return True
            return False
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        """Follow the cursor while the overlay has a mouse grab."""
        if event is not None and self._press_global is not None:
            self._update_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        """Finish a drag started from chrome or the collapsed bubble."""
        if (
            event is not None
            and event.button() == Qt.MouseButton.LeftButton
            and self._press_global is not None
        ):
            from_bubble = self._press_on_bubble
            was_click = self._end_drag()
            if was_click and from_bubble and not self._expanded:
                self._set_expanded(True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Session overlay collapses; idle overlay re-docks."""
        self._save_geometry()
        if self._session_active:
            self._set_expanded(False)
            if event is not None:
                event.ignore()
            return
        self.reattach_requested.emit()
        if event is not None:
            event.accept()
