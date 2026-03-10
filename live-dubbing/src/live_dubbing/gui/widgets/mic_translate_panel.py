"""
Mic Translate dock-widget panel.

Lets the user speak into their real microphone and have the translated
speech played through VB-Cable Input so other apps (Discord, Zoom, etc.)
receive it as virtual microphone input.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import structlog
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from live_dubbing.audio.mic_capture import get_input_devices
from live_dubbing.audio.playback import get_output_devices
from live_dubbing.audio.routing import VirtualAudioRouter
from live_dubbing.core.events import Event, EventBus, EventType
from live_dubbing.gui.languages import get_target_languages
from live_dubbing.gui.widgets.audio_meter import AudioMeter

if TYPE_CHECKING:
    from live_dubbing.app import AsyncWorker
    from live_dubbing.config.settings import AppSettings
    from live_dubbing.core.mic_translator import MicTranslator
    from live_dubbing.core.orchestrator import Orchestrator

logger = structlog.get_logger(__name__)


class MicTranslateWidget(QWidget):
    """
    Reusable widget for real-time microphone translation.

    Can be embedded in the main window or inside a QDockWidget (MicTranslatePanel).
    """

    def __init__(
        self,
        mic_translator: MicTranslator,
        orchestrator: Orchestrator,
        event_bus: EventBus,
        settings: AppSettings,
        async_worker: AsyncWorker | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mic_translator = mic_translator
        self._orchestrator = orchestrator
        self._event_bus = event_bus
        self._settings = settings
        self._async_worker = async_worker

        self._unsubscribers: list = []
        self._is_capturing_voice = False
        self._capture_timer: QTimer | None = None
        self._capture_elapsed_sec = 0.0

        self._setup_ui()
        self._populate_devices()
        self._populate_voices()
        self._connect_events()

    def cleanup(self) -> None:
        """Unsubscribe events, stop capture timer, and stop translator if running."""
        if self._capture_timer:
            self._capture_timer.stop()
            self._capture_timer = None

        for unsub in self._unsubscribers:
            with contextlib.suppress(Exception):
                unsub()
        self._unsubscribers.clear()

        if self._mic_translator.is_running and self._async_worker:
            self._async_worker.run_coroutine(self._mic_translator.stop())

    # -- UI setup --

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # -- Device / language selection --
        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout(config_group)
        config_layout.setSpacing(6)

        # Microphone row
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone:"))
        self._mic_combo = QComboBox()
        self._mic_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        mic_row.addWidget(self._mic_combo, 1)
        self._refresh_mic_btn = QPushButton("R")
        self._refresh_mic_btn.setFixedWidth(28)
        self._refresh_mic_btn.setToolTip("Refresh microphone list")
        self._refresh_mic_btn.clicked.connect(self._populate_devices)
        mic_row.addWidget(self._refresh_mic_btn)
        config_layout.addLayout(mic_row)

        # Target language row
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Target language:"))
        self._lang_combo = QComboBox()
        self._lang_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        for code, name in get_target_languages():
            self._lang_combo.addItem(name, code)
        lang_row.addWidget(self._lang_combo, 1)
        config_layout.addLayout(lang_row)

        # Output device row (virtual cable for other apps)
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output device:"))
        self._output_combo = QComboBox()
        self._output_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._output_combo.setToolTip(
            "Plays as virtual microphone for other apps (e.g. Discord, Zoom). "
            "Select CABLE Input (VB-Cable) so other apps receive translated audio as mic input."
        )
        out_row.addWidget(self._output_combo, 1)
        config_layout.addLayout(out_row)

        # Monitor device row (speakers to hear yourself)
        monitor_row = QHBoxLayout()
        monitor_row.addWidget(QLabel("Monitor output:"))
        self._monitor_combo = QComboBox()
        self._monitor_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._monitor_combo.setToolTip("Speakers/headphones to hear the translated audio yourself")
        monitor_row.addWidget(self._monitor_combo, 1)
        config_layout.addLayout(monitor_row)

        root.addWidget(config_group)

        # -- Voice selection --
        voice_group = QGroupBox("Voice")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setSpacing(6)

        # Voice selection row
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Output voice:"))
        self._voice_combo = QComboBox()
        self._voice_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._voice_combo.setToolTip("Select a cloned voice for TTS output")
        self._voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        voice_row.addWidget(self._voice_combo, 1)
        self._refresh_voice_btn = QPushButton("R")
        self._refresh_voice_btn.setFixedWidth(28)
        self._refresh_voice_btn.setToolTip("Refresh voice list")
        self._refresh_voice_btn.clicked.connect(self._populate_voices)
        voice_row.addWidget(self._refresh_voice_btn)
        self._rename_voice_btn = QPushButton("Rename")
        self._rename_voice_btn.setToolTip("Rename selected cloned voice")
        self._rename_voice_btn.setEnabled(False)
        self._rename_voice_btn.clicked.connect(self._on_rename_voice_clicked)
        voice_row.addWidget(self._rename_voice_btn)
        voice_layout.addLayout(voice_row)

        # Voice capture controls
        capture_row = QHBoxLayout()
        self._voice_name_input = QLineEdit()
        self._voice_name_input.setPlaceholderText("New voice name...")
        self._voice_name_input.setToolTip("Enter a name for the new voice clone")
        capture_row.addWidget(self._voice_name_input, 1)

        self._capture_btn = QPushButton("Capture")
        self._capture_btn.setToolTip("Start capturing audio for voice cloning")
        self._capture_btn.clicked.connect(self._on_capture_clicked)
        capture_row.addWidget(self._capture_btn)

        self._import_btn = QPushButton("Import...")
        self._import_btn.setToolTip("Import voice from audio file")
        self._import_btn.clicked.connect(self._on_import_clicked)
        capture_row.addWidget(self._import_btn)
        voice_layout.addLayout(capture_row)

        # Capture progress bar (hidden by default)
        self._capture_progress = QProgressBar()
        self._capture_progress.setRange(0, 100)
        self._capture_progress.setValue(0)
        self._capture_progress.setTextVisible(True)
        self._capture_progress.setFormat("Capturing: %p% (speak now)")
        self._capture_progress.setVisible(False)
        voice_layout.addWidget(self._capture_progress)

        # Voice status label
        self._voice_status = QLabel("")
        self._voice_status.setStyleSheet("color: gray; font-size: 11px;")
        self._voice_status.setVisible(False)
        voice_layout.addWidget(self._voice_status)

        root.addWidget(voice_group)

        # -- Start / Stop buttons --
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Mic Translate")
        self._start_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #1a7a1a;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #228a22; }
            QPushButton:disabled { background-color: #2a2a2a; color: #666; }
            """
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(self._start_btn, 1)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #7a1a1a;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #8a2222; }
            QPushButton:disabled { background-color: #2a2a2a; color: #666; }
            """
        )
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(self._stop_btn, 1)
        root.addLayout(btn_row)

        # -- Status label --
        self._status_label = QLabel("Idle")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: gray; font-size: 12px;")
        root.addWidget(self._status_label)

        # -- Audio meter --
        meter_frame = QFrame()
        meter_layout = QHBoxLayout(meter_frame)
        meter_layout.setContentsMargins(0, 0, 0, 0)
        self._audio_meter = AudioMeter(label="Mic Level", parent=self)
        meter_layout.addWidget(self._audio_meter, 1)
        root.addWidget(meter_frame)

        # -- Transcription box --
        you_group = QGroupBox("You said:")
        you_layout = QVBoxLayout(you_group)
        you_layout.setContentsMargins(6, 6, 6, 6)
        self._transcript_text = QTextEdit()
        self._transcript_text.setReadOnly(True)
        self._transcript_text.setMaximumHeight(80)
        self._transcript_text.setStyleSheet(
            """
            QTextEdit {
                background-color: #1e1e1e;
                color: #ddd;
                font-size: 12px;
                border: none;
            }
            """
        )
        you_layout.addWidget(self._transcript_text)
        root.addWidget(you_group)

        # -- Translation box --
        trans_group = QGroupBox("Translated:")
        trans_layout = QVBoxLayout(trans_group)
        trans_layout.setContentsMargins(6, 6, 6, 6)
        self._translation_text = QTextEdit()
        self._translation_text.setReadOnly(True)
        self._translation_text.setMaximumHeight(80)
        self._translation_text.setStyleSheet(
            """
            QTextEdit {
                background-color: #1e1e1e;
                color: #4CAF50;
                font-size: 12px;
                border: none;
            }
            """
        )
        trans_layout.addWidget(self._translation_text)
        root.addWidget(trans_group)

        root.addStretch()

    # -- Device population --

    @pyqtSlot()
    def _populate_devices(self) -> None:
        """Fill microphone and output-device combo boxes."""
        # --- Microphone ---
        self._mic_combo.blockSignals(True)
        prev_mic = self._mic_combo.currentData()
        self._mic_combo.clear()
        for dev_id, name in get_input_devices():
            self._mic_combo.addItem(name, dev_id)
        if prev_mic is not None:
            for i in range(self._mic_combo.count()):
                if self._mic_combo.itemData(i) == prev_mic:
                    self._mic_combo.setCurrentIndex(i)
                    break
        self._mic_combo.blockSignals(False)

        # --- Output (playback) devices ---
        self._output_combo.blockSignals(True)
        prev_out = self._output_combo.currentData()
        self._output_combo.clear()
        cable_index = -1
        for idx, (dev_id, name) in enumerate(get_output_devices()):
            display = name
            if "CABLE Input" in name or ("cable" in name.lower() and "input" in name.lower()):
                display = f"{name}  <- VB-Cable"
                cable_index = idx
            self._output_combo.addItem(display, dev_id)
            if "CABLE Input" in name or ("cable" in name.lower() and "input" in name.lower()):
                self._output_combo.setItemData(
                    idx,
                    "Routes audio as virtual microphone input to other apps",
                    Qt.ItemDataRole.ToolTipRole,
                )

        if cable_index >= 0:
            self._output_combo.setCurrentIndex(cable_index)
        else:
            try:
                router = VirtualAudioRouter()
                vb = router.get_vb_cable() if hasattr(router, "get_vb_cable") else None
                if vb and vb.input_id:
                    for i in range(self._output_combo.count()):
                        if self._output_combo.itemData(i) == vb.input_id:
                            self._output_combo.setCurrentIndex(i)
                            break
            except Exception:
                pass

        if prev_out is not None:
            for i in range(self._output_combo.count()):
                if self._output_combo.itemData(i) == prev_out:
                    self._output_combo.setCurrentIndex(i)
                    break
        self._output_combo.blockSignals(False)

        # --- Monitor (speakers) devices ---
        self._monitor_combo.blockSignals(True)
        prev_mon = self._monitor_combo.currentData()
        self._monitor_combo.clear()
        self._monitor_combo.addItem("None (no monitor)", "")
        for dev_id, name in get_output_devices():
            if "cable" in name.lower() and "input" in name.lower():
                continue
            self._monitor_combo.addItem(name, dev_id)
        if prev_mon is not None:
            for i in range(self._monitor_combo.count()):
                if self._monitor_combo.itemData(i) == prev_mon:
                    self._monitor_combo.setCurrentIndex(i)
                    break
        self._monitor_combo.blockSignals(False)

    # -- Voice population --

    @pyqtSlot()
    def _populate_voices(self) -> None:
        """Fill voice selection combo box from saved/cloned voices."""
        self._voice_combo.blockSignals(True)
        prev_voice = self._voice_combo.currentData()
        self._voice_combo.clear()

        self._voice_combo.addItem("Default (Rachel)", "")

        try:
            voices = self._orchestrator.get_saved_voices()
            for voice in voices:
                display_name = voice.name
                if voice.is_dynamic:
                    display_name = f"{voice.name} (cloned)"
                self._voice_combo.addItem(display_name, voice.voice_id)
        except Exception as e:
            logger.warning("Failed to load voices", error=str(e))

        restored = False
        if prev_voice:
            for i in range(self._voice_combo.count()):
                if self._voice_combo.itemData(i) == prev_voice:
                    self._voice_combo.setCurrentIndex(i)
                    restored = True
                    break

        if not restored and self._settings.voice_clone.default_voice_id:
            for i in range(self._voice_combo.count()):
                if self._voice_combo.itemData(i) == self._settings.voice_clone.default_voice_id:
                    self._voice_combo.setCurrentIndex(i)
                    break

        self._voice_combo.blockSignals(False)
        self._rename_voice_btn.setEnabled(bool(self._voice_combo.currentData()))

    # -- Voice handlers --

    @pyqtSlot(int)
    def _on_voice_changed(self, index: int) -> None:
        """Handle voice selection change."""
        voice_id = self._voice_combo.currentData()
        self._rename_voice_btn.setEnabled(bool(voice_id))
        if not voice_id:
            self._settings.voice_clone.default_voice_id = None
            return

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.switch_voice(voice_id),
                on_error=lambda e: logger.error("Failed to switch voice", error=e),
            )

    @pyqtSlot()
    def _on_rename_voice_clicked(self) -> None:
        """Rename the selected cloned voice."""
        voice_id = self._voice_combo.currentData()
        if not voice_id:
            return
        current = self._voice_combo.currentText().replace(" (cloned)", "").strip()
        new_name, ok = QInputDialog.getText(
            self,
            "Rename voice",
            "New name:",
            text=current,
        )
        if ok and new_name and new_name.strip():
            if self._orchestrator.rename_voice(voice_id, new_name.strip()):
                self._populate_voices()
            else:
                logger.warning("Rename failed", voice_id=voice_id)
                self._voice_status.setText("Rename failed. Please try again.")
                self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")
                self._voice_status.setVisible(True)

    @pyqtSlot()
    def _on_capture_clicked(self) -> None:
        """Handle capture button click - toggle capture state."""
        if self._is_capturing_voice:
            self._finish_voice_capture()
        else:
            self._start_voice_capture()

    def _start_voice_capture(self) -> None:
        """Begin capturing audio for voice cloning."""
        voice_name = self._voice_name_input.text().strip()
        if not voice_name:
            self._voice_status.setText("Please enter a voice name")
            self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")
            self._voice_status.setVisible(True)
            return

        self._is_capturing_voice = True
        self._capture_elapsed_sec = 0.0
        self._capture_btn.setText("Stop & Clone")
        self._capture_btn.setStyleSheet(
            "QPushButton { background-color: #7a1a1a; color: white; }"
        )
        self._voice_name_input.setEnabled(False)
        self._import_btn.setEnabled(False)
        self._capture_progress.setValue(0)
        self._capture_progress.setVisible(True)
        self._voice_status.setText("Speak clearly into your microphone...")
        self._voice_status.setStyleSheet("color: #4CAF50; font-size: 11px;")
        self._voice_status.setVisible(True)

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.start_voice_capture(voice_name),
                on_error=self._on_capture_error,
            )
        else:
            self._on_capture_error("Application not ready (no async worker).")
            return

        self._capture_timer = QTimer(self)
        self._capture_timer.timeout.connect(self._update_capture_progress)
        self._capture_timer.start(100)

    def _update_capture_progress(self) -> None:
        """Update capture progress bar."""
        self._capture_elapsed_sec += 0.1
        min_duration = self._settings.voice_clone.dynamic_capture_duration_sec
        max_duration = 30.0

        progress = min(100, int((self._capture_elapsed_sec / min_duration) * 100))
        self._capture_progress.setValue(progress)

        if self._capture_elapsed_sec >= min_duration:
            self._capture_progress.setFormat(f"Ready to clone ({self._capture_elapsed_sec:.1f}s)")
        else:
            remaining = min_duration - self._capture_elapsed_sec
            self._capture_progress.setFormat(f"Capturing: {remaining:.1f}s remaining")

        if self._capture_elapsed_sec >= max_duration:
            self._finish_voice_capture()

    def _finish_voice_capture(self) -> None:
        """Finish capturing and create the voice clone."""
        if self._capture_timer:
            self._capture_timer.stop()
            self._capture_timer = None

        self._capture_btn.setText("Capture")
        self._capture_btn.setStyleSheet("")
        self._voice_name_input.setEnabled(True)
        self._import_btn.setEnabled(True)
        self._capture_progress.setFormat("Creating voice clone...")
        self._voice_status.setText("Processing voice clone...")
        self._voice_status.setStyleSheet("color: #FFC107; font-size: 11px;")

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.finish_voice_capture(),
                on_success=self._on_capture_success,
                on_error=self._on_capture_error,
            )

        self._is_capturing_voice = False

    def _on_capture_success(self, voice: object) -> None:
        """Called when voice capture completes successfully."""
        self._capture_progress.setVisible(False)
        if voice:
            self._voice_status.setText("Voice cloned successfully!")
            self._voice_status.setStyleSheet("color: #4CAF50; font-size: 11px;")
            self._voice_name_input.clear()
            QTimer.singleShot(500, self._populate_voices)
        else:
            self._voice_status.setText("Voice capture failed - not enough audio")
            self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")

    def _on_capture_error(self, error_msg: str) -> None:
        """Called when voice capture fails."""
        logger.error("Voice capture failed", error=error_msg)
        self._is_capturing_voice = False
        if self._capture_timer:
            self._capture_timer.stop()
            self._capture_timer = None

        self._capture_btn.setText("Capture")
        self._capture_btn.setStyleSheet("")
        self._voice_name_input.setEnabled(True)
        self._import_btn.setEnabled(True)
        self._capture_progress.setVisible(False)
        self._voice_status.setText(f"Error: {error_msg[:50]}")
        self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")

    @pyqtSlot()
    def _on_import_clicked(self) -> None:
        """Handle import button click - open file dialog."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Voice from Audio File",
            "",
            "Audio Files (*.wav *.mp3 *.m4a *.flac *.ogg);;All Files (*)",
        )
        if not file_path:
            return

        voice_name = self._voice_name_input.text().strip()
        if not voice_name:
            voice_name = os.path.splitext(os.path.basename(file_path))[0]
            self._voice_name_input.setText(voice_name)

        self._voice_status.setText("Importing voice from file...")
        self._voice_status.setStyleSheet("color: #FFC107; font-size: 11px;")
        self._voice_status.setVisible(True)
        self._import_btn.setEnabled(False)

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.clone_voice_from_file(file_path, voice_name),
                on_success=self._on_import_success,
                on_error=self._on_import_error,
            )
        else:
            self._on_import_error("Application not ready (no async worker).")

    def _on_import_success(self, voice: object) -> None:
        """Called when voice import completes successfully."""
        self._import_btn.setEnabled(True)
        if voice:
            self._voice_status.setText("Voice imported successfully!")
            self._voice_status.setStyleSheet("color: #4CAF50; font-size: 11px;")
            self._voice_name_input.clear()
            QTimer.singleShot(500, self._populate_voices)
        else:
            self._voice_status.setText("Voice import failed")
            self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")

    def _on_import_error(self, error_msg: str) -> None:
        """Called when voice import fails."""
        logger.error("Voice import failed", error=error_msg)
        self._import_btn.setEnabled(True)
        self._voice_status.setText(f"Import error: {error_msg[:50]}")
        self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")

    # -- Event subscriptions --

    def _connect_events(self) -> None:
        unsub = self._event_bus.subscribe(
            EventType.MIC_TRANSCRIPTION_UPDATE, self._on_transcription_update
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.MIC_TRANSLATION_UPDATE, self._on_translation_update
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.AUDIO_LEVEL_UPDATE, self._on_audio_level_update
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.MIC_TRANSLATE_STARTED, self._on_mic_started
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.MIC_TRANSLATE_STOPPED, self._on_mic_stopped
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_COMPLETED, self._on_voice_clone_completed
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_FAILED, self._on_voice_clone_failed
        )
        self._unsubscribers.append(unsub)

    # -- Button handlers --

    @pyqtSlot()
    def _on_start_clicked(self) -> None:
        """Start mic translation session."""
        if self._mic_translator.is_running:
            return

        mic_id = self._mic_combo.currentData() or None
        lang_code = self._lang_combo.currentData() or "es"
        output_id = self._output_combo.currentData() or None
        monitor_id = self._monitor_combo.currentData() or None
        voice_id = self._voice_combo.currentData() or None

        self._start_btn.setEnabled(False)
        self._status_label.setText("Starting...")
        self._status_label.setStyleSheet("color: #FFC107; font-size: 12px;")

        if not self._async_worker:
            self._on_start_error("Application not ready (no async worker).")
            return
        self._async_worker.run_coroutine(
                self._mic_translator.start(
                    elevenlabs_service=self._orchestrator.elevenlabs_service,
                    mic_device_id=mic_id,
                    target_language=lang_code,
                    output_device_id=output_id,
                    monitor_device_id=monitor_id,
                    premade_voice_id=voice_id,
                ),
                on_error=self._on_start_error,
            )

    @pyqtSlot()
    def _on_stop_clicked(self) -> None:
        """Stop mic translation session."""
        if not self._mic_translator.is_running:
            return

        self._stop_btn.setEnabled(False)
        self._status_label.setText("Stopping...")
        self._status_label.setStyleSheet("color: #FFC107; font-size: 12px;")

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._mic_translator.stop(),
                on_error=self._on_stop_error,
            )

    def _on_start_error(self, error_msg: str) -> None:
        """Called on the Qt thread when start() raises."""
        logger.error("Failed to start mic translator", error=error_msg)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_label.setText(f"Error: {error_msg[:80]}")
        self._status_label.setStyleSheet("color: #F44336; font-size: 12px;")

    def _on_stop_error(self, error_msg: str) -> None:
        """Called on the Qt thread when stop() raises."""
        logger.error("Error stopping mic translator", error=error_msg)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_label.setText("Stopped (with error)")
        self._status_label.setStyleSheet("color: #F44336; font-size: 12px;")

    # -- Event handlers (run on Qt main thread via EventBus signal) --

    @pyqtSlot(object)
    def _on_mic_started(self, event: Event) -> None:
        lang = event.data.get("target_language", "")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText(f"Listening -> {lang.upper()}")
        self._status_label.setStyleSheet("color: #4CAF50; font-size: 12px; font-weight: bold;")
        self._mic_combo.setEnabled(False)
        self._lang_combo.setEnabled(False)
        self._output_combo.setEnabled(False)
        self._monitor_combo.setEnabled(False)
        self._voice_combo.setEnabled(False)
        self._refresh_mic_btn.setEnabled(False)
        self._refresh_voice_btn.setEnabled(False)

    @pyqtSlot(object)
    def _on_mic_stopped(self, event: Event) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_label.setText("Idle")
        self._status_label.setStyleSheet("color: gray; font-size: 12px;")
        self._mic_combo.setEnabled(True)
        self._lang_combo.setEnabled(True)
        self._output_combo.setEnabled(True)
        self._monitor_combo.setEnabled(True)
        self._voice_combo.setEnabled(True)
        self._refresh_mic_btn.setEnabled(True)
        self._refresh_voice_btn.setEnabled(True)
        self._audio_meter.set_level(0.0)

    @pyqtSlot(object)
    def _on_transcription_update(self, event: Event) -> None:
        text = event.data.get("text", "")
        if text:
            self._transcript_text.setPlainText(text)

    @pyqtSlot(object)
    def _on_translation_update(self, event: Event) -> None:
        text = event.data.get("text", "")
        if text:
            self._translation_text.setPlainText(text)

    @pyqtSlot(object)
    def _on_audio_level_update(self, event: Event) -> None:
        if event.data.get("source") != "mic":
            return
        level = event.data.get("level", 0.0)
        is_speech = event.data.get("is_speech", False)
        self._audio_meter.set_level(level, is_speech)

    @pyqtSlot(object)
    def _on_voice_clone_completed(self, event: Event) -> None:
        """Handle voice clone completion event."""
        voice_id = event.data.get("voice_id")
        name = event.data.get("name", "Unknown")
        logger.info("Voice clone completed", voice_id=voice_id, name=name)
        QTimer.singleShot(100, self._populate_voices)

    @pyqtSlot(object)
    def _on_voice_clone_failed(self, event: Event) -> None:
        """Handle voice clone failure event."""
        error = event.data.get("error", "Unknown error")
        logger.error("Voice clone failed", error=error)
        self._voice_status.setText(f"Clone failed: {str(error)[:40]}")
        self._voice_status.setStyleSheet("color: #F44336; font-size: 11px;")
        self._voice_status.setVisible(True)


class MicTranslatePanel(QDockWidget):
    """
    Dockable panel for real-time microphone translation.

    Wraps MicTranslateWidget for use as a dock widget. The same widget can be
    embedded in the main window.
    """

    def __init__(
        self,
        mic_translator: MicTranslator,
        orchestrator: Orchestrator,
        event_bus: EventBus,
        settings: AppSettings,
        async_worker: AsyncWorker | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Mic Translate", parent)
        self._content = MicTranslateWidget(
            mic_translator=mic_translator,
            orchestrator=orchestrator,
            event_bus=event_bus,
            settings=settings,
            async_worker=async_worker,
            parent=self,
        )
        self.setWidget(self._content)
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setMinimumSize(360, 520)

    def closeEvent(self, event: QCloseEvent | None) -> None:  # type: ignore[override]
        """Unsubscribe events and stop translator on close."""
        self._content.cleanup()
        if event is not None:
            super().closeEvent(event)
