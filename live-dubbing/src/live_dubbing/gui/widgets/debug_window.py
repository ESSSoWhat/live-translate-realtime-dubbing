"""
Debug window widget for viewing real-time processing information.
"""

import contextlib
from collections import deque
from datetime import datetime

import structlog
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QCloseEvent, QColor, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from live_dubbing.core.events import Event, EventBus, EventType
from live_dubbing.core.orchestrator import Orchestrator
from live_dubbing.core.state import ApplicationStateSnapshot

logger = structlog.get_logger(__name__)


class QueueIndicator(QFrame):
    """Small indicator showing queue depth."""

    def __init__(
        self, name: str, max_size: int = 50, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._max_size = max_size
        self._current = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._label = QLabel(self._name)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("font-size: 10px;")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setMinimum(0)
        self._bar.setMaximum(self._max_size)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        self._bar.setFormat("%v")
        self._bar.setMaximumHeight(16)
        layout.addWidget(self._bar)

        self.setFrameStyle(QFrame.Shape.Box)
        self.setMinimumWidth(60)

    def set_depth(self, depth: int) -> None:
        """Update queue depth display."""
        self._current = depth
        self._bar.setValue(min(depth, self._max_size))

        # Color code based on fill level
        fill_percent = depth / self._max_size if self._max_size > 0 else 0
        if fill_percent < 0.5:
            color = "#4CAF50"  # Green
        elif fill_percent < 0.8:
            color = "#FFC107"  # Yellow
        else:
            color = "#F44336"  # Red

        self._bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; }}")


class PipelineStatusPanel(QGroupBox):
    """Panel showing pipeline state and queue depths."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Pipeline Status", parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Pipeline state
        state_layout = QHBoxLayout()
        state_layout.addWidget(QLabel("State:"))
        self._state_label = QLabel("IDLE")
        self._state_label.setStyleSheet("font-weight: bold;")
        state_layout.addWidget(self._state_label)
        state_layout.addStretch()
        layout.addLayout(state_layout)

        # Queue depths header
        queue_header = QLabel("Queue Depths:")
        queue_header.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(queue_header)

        # Queue indicators (horizontal layout)
        queue_layout = QHBoxLayout()

        self._vad_queue = QueueIndicator("VAD", 50)
        self._stt_queue = QueueIndicator("STT", 20)
        self._tts_queue = QueueIndicator("TTS", 20)
        self._output_queue = QueueIndicator("Out", 50)

        queue_layout.addWidget(self._vad_queue)
        queue_layout.addWidget(self._stt_queue)
        queue_layout.addWidget(self._tts_queue)
        queue_layout.addWidget(self._output_queue)

        layout.addLayout(queue_layout)

        # Stats section
        stats_layout = QHBoxLayout()

        self._chunks_label = QLabel("Chunks: 0")
        self._chunks_label.setStyleSheet("font-size: 11px;")
        stats_layout.addWidget(self._chunks_label)

        self._audio_sec_label = QLabel("Audio: 0.0s")
        self._audio_sec_label.setStyleSheet("font-size: 11px;")
        stats_layout.addWidget(self._audio_sec_label)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

    def update_from_snapshot(self, snapshot: ApplicationStateSnapshot) -> None:
        """Update panel from ApplicationStateSnapshot."""
        # Update state
        state_name = snapshot.app_state.name if snapshot.app_state else "UNKNOWN"
        self._state_label.setText(state_name)

        # Color code state
        state_colors = {
            "INITIALIZING": "#FFC107",
            "READY": "#4CAF50",
            "RUNNING": "#2196F3",
            "CONFIGURING": "#9C27B0",
            "STOPPING": "#FF9800",
            "ERROR": "#F44336",
        }
        color = state_colors.get(state_name, "#666")
        self._state_label.setStyleSheet(f"font-weight: bold; color: {color};")

        # Update stats
        if snapshot.pipeline_stats:
            stats = snapshot.pipeline_stats
            chunks = getattr(stats, "total_chunks_processed", 0)
            audio_sec = getattr(stats, "total_audio_duration_sec", 0.0)
            self._chunks_label.setText(f"Chunks: {chunks}")
            self._audio_sec_label.setText(f"Audio: {audio_sec:.1f}s")

    def update_queue_depths(self, depths: dict[str, int]) -> None:
        """Update queue depth indicators."""
        self._vad_queue.set_depth(depths.get("vad", 0))
        self._stt_queue.set_depth(depths.get("stt", 0))
        self._tts_queue.set_depth(depths.get("tts", 0))
        self._output_queue.set_depth(depths.get("output", 0))


class LatencyGraph(QWidget):
    """Custom widget for drawing latency history graph."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[float] = []
        self._max_value = 3000  # 3 seconds max display
        self.setMinimumHeight(60)

    def set_data(self, data: list[float]) -> None:
        """Set the latency history data."""
        self._data = data
        self.update()  # Trigger repaint

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """Draw the latency graph."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        painter.fillRect(self.rect(), QColor("#1a1a1a"))

        height = self.height()
        width = self.width()

        # Draw horizontal grid lines at 1s and 2s
        painter.setPen(QPen(QColor("#333"), 1))
        for ms in [1000, 2000]:
            y = int(height - (ms / self._max_value) * height)
            painter.drawLine(0, y, width, y)

        # Draw threshold lines with labels
        # 1 second line (yellow dashed)
        painter.setPen(QPen(QColor("#FFC107"), 1, Qt.PenStyle.DashLine))
        y_1s = int(height - (1000 / self._max_value) * height)
        painter.drawLine(0, y_1s, width, y_1s)

        # 2 second line (red dashed)
        painter.setPen(QPen(QColor("#F44336"), 1, Qt.PenStyle.DashLine))
        y_2s = int(height - (2000 / self._max_value) * height)
        painter.drawLine(0, y_2s, width, y_2s)

        # Draw data line
        if len(self._data) < 2:
            return

        # Calculate points
        points = []
        for i, value in enumerate(self._data):
            x = int((i / (len(self._data) - 1)) * width) if len(self._data) > 1 else 0
            y = int(height - (min(value, self._max_value) / self._max_value) * height)
            points.append((x, y))

        # Draw line
        painter.setPen(QPen(QColor("#2196F3"), 2))
        for i in range(len(points) - 1):
            painter.drawLine(
                points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]
            )


class LatencyPanel(QGroupBox):
    """Panel showing latency metrics and history graph."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Latency", parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Current/Average display
        metrics_layout = QHBoxLayout()

        self._current_label = QLabel("Current: --ms")
        self._current_label.setStyleSheet("font-size: 12px;")
        metrics_layout.addWidget(self._current_label)

        self._average_label = QLabel("Average: --ms")
        self._average_label.setStyleSheet("font-size: 12px; color: gray;")
        metrics_layout.addWidget(self._average_label)

        metrics_layout.addStretch()
        layout.addLayout(metrics_layout)

        # Latency graph
        self._graph = LatencyGraph()
        self._graph.setMinimumHeight(80)
        layout.addWidget(self._graph)

    def update_latency(
        self,
        current: float,
        average: float,
        history: list[float],
    ) -> None:
        """Update latency display."""
        # Update labels
        self._current_label.setText(f"Current: {current:.0f}ms")
        self._average_label.setText(f"Average: {average:.0f}ms")

        # Color code current latency
        if current < 1000:
            color = "#4CAF50"
        elif current < 2000:
            color = "#FFC107"
        else:
            color = "#F44336"
        self._current_label.setStyleSheet(f"font-size: 12px; color: {color};")

        # Update graph
        self._graph.set_data(history)


class VoiceClonePanel(QGroupBox):
    """Panel showing voice clone status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Voice Clone", parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Status
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))
        self._status_label = QLabel("Not Started")
        self._status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        layout.addLayout(status_layout)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        layout.addWidget(self._progress)

        # Voice ID
        self._voice_id_label = QLabel("Voice ID: --")
        self._voice_id_label.setStyleSheet("font-size: 10px; color: gray;")
        self._voice_id_label.setWordWrap(True)
        layout.addWidget(self._voice_id_label)

    def set_progress(self, progress: float) -> None:
        """Update voice clone progress (0.0 to 1.0)."""
        self._status_label.setText("Capturing...")
        self._status_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        self._progress.setValue(int(progress * 100))

    def set_completed(self, voice_id: str) -> None:
        """Mark voice clone as completed."""
        self._status_label.setText("Ready")
        self._status_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        self._progress.setValue(100)
        self._voice_id_label.setText(f"Voice ID: {voice_id}")

    def reset(self) -> None:
        """Reset to initial state."""
        self._status_label.setText("Not Started")
        self._status_label.setStyleSheet("font-weight: bold;")
        self._progress.setValue(0)
        self._voice_id_label.setText("Voice ID: --")


class RealTimeTextPanel(QGroupBox):
    """Panel showing real-time transcription and translation text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Real-time Text", parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Transcription section
        trans_header = QHBoxLayout()
        trans_header.addWidget(QLabel("Transcription:"))
        self._trans_timestamp = QLabel("")
        self._trans_timestamp.setStyleSheet("font-size: 10px; color: gray;")
        trans_header.addWidget(self._trans_timestamp)
        trans_header.addStretch()
        layout.addLayout(trans_header)

        self._transcription_text = QLabel("--")
        self._transcription_text.setWordWrap(True)
        self._transcription_text.setStyleSheet(
            """
            QLabel {
                background-color: #2a2a2a;
                padding: 8px;
                border-radius: 4px;
                font-size: 12px;
            }
        """
        )
        self._transcription_text.setMinimumHeight(40)
        layout.addWidget(self._transcription_text)

        # Translation section
        transl_header = QHBoxLayout()
        transl_header.addWidget(QLabel("Translation:"))
        self._transl_timestamp = QLabel("")
        self._transl_timestamp.setStyleSheet("font-size: 10px; color: gray;")
        transl_header.addWidget(self._transl_timestamp)
        transl_header.addStretch()
        layout.addLayout(transl_header)

        self._translation_text = QLabel("--")
        self._translation_text.setWordWrap(True)
        self._translation_text.setStyleSheet(
            """
            QLabel {
                background-color: #2a2a2a;
                padding: 8px;
                border-radius: 4px;
                font-size: 12px;
                color: #4CAF50;
            }
        """
        )
        self._translation_text.setMinimumHeight(40)
        layout.addWidget(self._translation_text)

    def set_transcription(self, text: str, language: str = "") -> None:
        """Update transcription text."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._trans_timestamp.setText(f"[{timestamp}] {language}")
        self._transcription_text.setText(text if text else "--")

    def set_translation(self, text: str) -> None:
        """Update translation text."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._transl_timestamp.setText(f"[{timestamp}]")
        self._translation_text.setText(text if text else "--")


class EventLogPanel(QGroupBox):
    """Panel showing scrolling event log with filtering."""

    MAX_ENTRIES = 200

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Event Log", parent)
        self._events: deque[dict] = deque(maxlen=self.MAX_ENTRIES)
        self._filter_type: EventType | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = QHBoxLayout()

        # Filter combo
        toolbar.addWidget(QLabel("Filter:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("All Events", None)
        for event_type in EventType:
            self._filter_combo.addItem(event_type.name, event_type)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo)

        toolbar.addStretch()

        # Clear button
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_log)
        self._clear_btn.setMaximumWidth(60)
        toolbar.addWidget(self._clear_btn)

        layout.addLayout(toolbar)

        # Log text area
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(
            """
            QTextEdit {
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 11px;
                background-color: #1a1a1a;
                color: #ddd;
            }
        """
        )
        layout.addWidget(self._log_text)

    def add_event(self, event: Event) -> None:
        """Add an event to the log."""
        entry = {
            "timestamp": datetime.now(),
            "type": event.type,
            "data": event.data,
        }
        self._events.append(entry)

        # Only display if matches filter
        if self._filter_type is None or event.type == self._filter_type:
            self._append_entry(entry)

    def _append_entry(self, entry: dict) -> None:
        """Append a log entry to the display."""
        timestamp = entry["timestamp"].strftime("%H:%M:%S.%f")[:-3]
        event_type = entry["type"].name

        # Color code by event category
        color = self._get_event_color(entry["type"])

        # Format data summary
        data_summary = ""
        if entry["data"]:
            data_items = [f"{k}={v}" for k, v in list(entry["data"].items())[:3]]
            data_summary = " | " + ", ".join(data_items)
            # Truncate if too long
            if len(data_summary) > 60:
                data_summary = data_summary[:57] + "..."

        html = f'<span style="color: gray;">{timestamp}</span> '
        html += f'<span style="color: {color};">[{event_type}]</span>'
        html += f'<span style="color: #aaa;">{data_summary}</span><br/>'

        self._log_text.insertHtml(html)

        # Auto-scroll to bottom
        scrollbar = self._log_text.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def _get_event_color(self, event_type: EventType) -> str:
        """Get color for event type."""
        name = event_type.name
        if "ERROR" in name:
            return "#F44336"
        elif "WARNING" in name:
            return "#FFC107"
        elif "COMPLETED" in name or "STARTED" in name:
            return "#4CAF50"
        elif "UPDATE" in name:
            return "#2196F3"
        else:
            return "#ddd"

    def _on_filter_changed(self, index: int) -> None:
        """Handle filter selection change."""
        self._filter_type = self._filter_combo.itemData(index)
        self._refresh_display()

    def _refresh_display(self) -> None:
        """Refresh the log display with current filter."""
        self._log_text.clear()
        for entry in self._events:
            if self._filter_type is None or entry["type"] == self._filter_type:
                self._append_entry(entry)

    def _clear_log(self) -> None:
        """Clear the event log."""
        self._events.clear()
        self._log_text.clear()


class DebugWindow(QDockWidget):
    """
    Dockable debug window for monitoring real-time processing.

    Shows pipeline status, latency graphs, event logs, voice clone status,
    and real-time transcription/translation text.
    """

    MAX_LATENCY_HISTORY = 60  # Number of data points for latency graph
    STATS_UPDATE_INTERVAL_MS = 250  # 250ms refresh rate

    def __init__(
        self,
        orchestrator: Orchestrator,
        event_bus: EventBus,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Debug Monitor", parent)
        self._orchestrator = orchestrator
        self._event_bus = event_bus

        # Data storage
        self._latency_history: deque[float] = deque(maxlen=self.MAX_LATENCY_HISTORY)

        # Unsubscribers for cleanup
        self._unsubscribers: list = []

        self._setup_ui()
        self._connect_events()
        self._setup_timer()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # Main container widget
        container = QWidget()
        self.setWidget(container)

        main_layout = QVBoxLayout(container)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Use a splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top row: Pipeline Status + Voice Clone
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self._pipeline_panel = PipelineStatusPanel()
        top_layout.addWidget(self._pipeline_panel, 2)

        self._voice_panel = VoiceClonePanel()
        top_layout.addWidget(self._voice_panel, 1)

        splitter.addWidget(top_row)

        # Middle: Latency Panel
        self._latency_panel = LatencyPanel()
        splitter.addWidget(self._latency_panel)

        # Real-time Text Panel
        self._text_panel = RealTimeTextPanel()
        splitter.addWidget(self._text_panel)

        # Bottom: Event Log
        self._event_log_panel = EventLogPanel()
        splitter.addWidget(self._event_log_panel)

        main_layout.addWidget(splitter)

        # Configure dock widget properties
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.setMinimumSize(400, 500)

    def _connect_events(self) -> None:
        """Subscribe to relevant events from EventBus."""
        # All events for logging
        for event_type in EventType:
            unsub = self._event_bus.subscribe(event_type, self._on_any_event)
            self._unsubscribers.append(unsub)

        # Specific events for dedicated panels
        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_PROGRESS, self._on_voice_clone_progress
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_COMPLETED, self._on_voice_clone_completed
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.TRANSCRIPTION_UPDATE, self._on_transcription_update
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.TRANSLATION_UPDATE, self._on_translation_update
        )
        self._unsubscribers.append(unsub)

    def _setup_timer(self) -> None:
        """Set up periodic stats update timer."""
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(self.STATS_UPDATE_INTERVAL_MS)

    @pyqtSlot()
    def _refresh_stats(self) -> None:
        """Periodic refresh of stats from orchestrator."""
        try:
            snapshot = self._orchestrator.get_state_snapshot()

            # Update pipeline panel
            self._pipeline_panel.update_from_snapshot(snapshot)

            # Update queue depths if available
            if hasattr(self._orchestrator, "get_pipeline_queue_depths"):
                depths = self._orchestrator.get_pipeline_queue_depths()
                self._pipeline_panel.update_queue_depths(depths)

            # Update latency history
            if snapshot.pipeline_stats:
                latency = getattr(snapshot.pipeline_stats, "current_latency_ms", 0.0)
                average = getattr(snapshot.pipeline_stats, "average_latency_ms", 0.0)
                self._latency_history.append(latency)
                self._latency_panel.update_latency(
                    current=latency,
                    average=average,
                    history=list(self._latency_history),
                )
        except Exception as e:
            logger.debug("Error refreshing debug stats", error=str(e))

    @pyqtSlot(object)
    def _on_any_event(self, event: Event) -> None:
        """Log all events to the event log panel."""
        self._event_log_panel.add_event(event)

    @pyqtSlot(object)
    def _on_voice_clone_progress(self, event: Event) -> None:
        """Handle voice clone progress update."""
        progress = event.data.get("progress", 0.0)
        self._voice_panel.set_progress(progress)

    @pyqtSlot(object)
    def _on_voice_clone_completed(self, event: Event) -> None:
        """Handle voice clone completion."""
        voice_id = event.data.get("voice_id", "")
        self._voice_panel.set_completed(voice_id)

    @pyqtSlot(object)
    def _on_transcription_update(self, event: Event) -> None:
        """Handle transcription update."""
        text = event.data.get("text", "")
        language = event.data.get("language", "")
        self._text_panel.set_transcription(text, language)

    @pyqtSlot(object)
    def _on_translation_update(self, event: Event) -> None:
        """Handle translation update."""
        text = event.data.get("text", "")
        self._text_panel.set_translation(text)

    def closeEvent(self, event: QCloseEvent | None) -> None:  # type: ignore[override]
        """Clean up on close."""
        self._stats_timer.stop()
        for unsub in self._unsubscribers:
            with contextlib.suppress(Exception):
                unsub()
        if event is not None:
            super().closeEvent(event)
