"""Main application window."""  # noqa: D200

# pylint: disable=E0611,W0611,C0415,C0301,C0103,W0212,W0613,W0718

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from live_dubbing.app import AsyncWorker
from PyQt6.QtCore import Qt, QTimer, pyqtSlot  # pylint: disable=no-name-in-module
from PyQt6.QtGui import QCloseEvent, QFont, QIcon, QKeySequence, QShortcut  # pylint: disable=no-name-in-module

# PyQt6 uses dynamic exports; Pylint cannot resolve them without the runtime env
from PyQt6.QtWidgets import (  # pylint: disable=no-name-in-module
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from live_dubbing.audio.playback import get_output_devices
from live_dubbing.audio.session import AudioSessionInfo
from live_dubbing.config.settings import AppSettings, ConfigManager
from live_dubbing.core.events import Event, EventBus, EventType
from live_dubbing.core.orchestrator import Orchestrator
from live_dubbing.core.state import AppState, TranslationState
from live_dubbing.gui.widgets.app_selector import AppSelectorWidget
from live_dubbing.gui.widgets.audio_meter import AudioMeter
from live_dubbing.gui.widgets.debug_window import DebugWindow
from live_dubbing.gui.widgets.language_panel import LanguagePanel
from live_dubbing.gui.widgets.status_bar import StatusBar
from live_dubbing.gui.widgets.dubbed_window import DubbedWindow
from live_dubbing.gui.widgets.vb_cable_wizard import VBCableSetupWizard

logger = structlog.get_logger(__name__)


class MainWindow(QMainWindow):
    """
    Main application window for Live Dubbing.

    Layout:
    - Top: App selector and language config
    - Middle: Status display and controls
    - Bottom: Live transcription/translation display
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        event_bus: EventBus,
        settings: AppSettings,
        async_worker: AsyncWorker | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the main window with orchestrator, event bus, and settings."""
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._event_bus = event_bus
        self._settings = settings
        self._async_worker = async_worker

        self._is_running = False
        self._unsubscribers: list = []
        self._use_system_fallback = False  # Track if using system loopback

        self._setup_window()
        self._setup_ui()
        self._setup_debug_window()
        self._setup_menus()
        self._setup_shortcuts()
        self._connect_events()
        self._setup_refresh_timer()

        # Populate saved voices on startup
        self._refresh_voice_list()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle("Live Translate - Real-time Translation")
        self.setMinimumSize(800, 600)

        # Set window icon
        import pathlib

        icon_path = pathlib.Path(__file__).parent / "assets" / "logo.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Set default size first
        self.resize(800, 600)

        # Restore window position if saved, with validation
        try:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geo = screen.availableGeometry()

                # Restore position only if within screen bounds
                if self._settings.ui.window_x is not None:
                    x = max(
                        0,
                        min(self._settings.ui.window_x, screen_geo.width() - 100),
                    )
                    y = max(
                        0,
                        min(
                            self._settings.ui.window_y or 0,
                            screen_geo.height() - 100,
                        ),
                    )
                    self.move(x, y)

                # Restore size only if reasonable
                ww = self._settings.ui.window_width
                if ww and ww > 100:
                    width = min(ww, screen_geo.width())
                    height = min(
                        self._settings.ui.window_height or 600,
                        screen_geo.height(),
                    )
                    self.resize(width, height)
        except Exception as e:
            logger.warning("Could not restore window position", error=str(e))

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Top section: Configuration
        config_layout = QHBoxLayout()

        # App selector (left)
        self._app_selector = AppSelectorWidget()
        self._app_selector.refresh_requested.connect(self._refresh_sessions)
        config_layout.addWidget(self._app_selector, 1)

        # Language panel (right)
        self._language_panel = LanguagePanel()
        config_layout.addWidget(self._language_panel, 1)

        main_layout.addLayout(config_layout)

        # API key missing banner (hidden by default)
        self._api_banner = QFrame()
        self._api_banner.setStyleSheet(
            """
            QFrame {
                background-color: #3d3500;
                border: 1px solid #665a00;
                border-radius: 4px;
                padding: 4px;
            }
            """
        )
        banner_layout = QHBoxLayout(self._api_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        banner_label = QLabel("API key not configured — voice cloning and TTS require an ElevenLabs key.")
        banner_label.setStyleSheet("color: #ffcc00; font-size: 12px;")
        banner_layout.addWidget(banner_label)
        banner_layout.addStretch()
        self._api_banner_btn = QPushButton("Open Settings")
        self._api_banner_btn.setFixedWidth(100)
        self._api_banner_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #665a00;
                color: #ffcc00;
                border-radius: 3px;
                padding: 4px 8px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #7a6b00; }
            """
        )
        self._api_banner_btn.clicked.connect(self._open_settings)
        banner_layout.addWidget(self._api_banner_btn)
        self._api_banner.setVisible(False)  # Hidden until we know key is missing
        main_layout.addWidget(self._api_banner)

        # Audio device section
        device_group = QGroupBox("Audio Devices")
        device_row = QHBoxLayout(device_group)

        # Capture mode selector
        device_row.addWidget(QLabel("Capture:"))
        self._capture_mode_combo = QComboBox()
        self._capture_mode_combo.addItem("System Audio (all audio, no setup)", "system")
        self._capture_mode_combo.addItem("VB-Cable (per-app, requires setup)", "vbcable")
        self._capture_mode_combo.setCurrentIndex(0)  # Default to system audio
        self._capture_mode_combo.setMinimumWidth(240)
        self._capture_mode_combo.setMaxVisibleItems(5)
        self._capture_mode_combo.setToolTip(
            "System Audio captures all system sound.\n"
            "VB-Cable routes per-app audio (requires VB-Cable installed)."
        )
        device_row.addWidget(self._capture_mode_combo)

        device_row.addSpacing(15)

        # Output device selector
        device_row.addWidget(QLabel("Output:"))
        self._output_device_combo = QComboBox()
        self._output_device_combo.setMinimumWidth(240)
        # Limit visible items in dropdown to prevent rendering issues
        self._output_device_combo.setMaxVisibleItems(10)
        # Use a standard view to avoid potential rendering crashes
        self._output_device_combo.setStyleSheet("")
        self._output_device_combo.setToolTip("Select the audio output device for dubbed speech")
        self._populate_output_devices()
        # Use activated signal (user-initiated only) instead of currentIndexChanged
        # to avoid crashes during programmatic changes
        self._output_device_combo.activated.connect(
            self._on_output_device_changed
        )
        device_row.addWidget(self._output_device_combo)
        device_row.addStretch()
        main_layout.addWidget(device_group)

        # Middle section: Controls and status
        control_group = QGroupBox("Controls")
        control_layout = QVBoxLayout(control_group)

        # Button row
        button_layout = QHBoxLayout()

        self._start_btn = QPushButton("Start Translation")
        self._start_btn.setMinimumHeight(40)
        self._start_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #666;
            }
            """
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setMinimumHeight(40)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:disabled {
                background-color: #666;
            }
            """
        )
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        button_layout.addWidget(self._stop_btn)

        control_layout.addLayout(button_layout)

        # ── Voice Panel ──────────────────────────────────────────────────
        voice_group = QGroupBox("Voices")
        voice_group.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #444;
                border-radius: 5px;
                margin-top: 8px;
                padding-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            """
        )
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setSpacing(6)

        # Auto-clone toggle
        self._auto_clone_cb = QCheckBox("Auto clone voice on start")
        self._auto_clone_cb.setChecked(self._settings.voice_clone.auto_clone_voice)
        self._auto_clone_cb.setToolTip(
            "Automatically capture and clone the speaker's voice when translation starts"
        )
        self._auto_clone_cb.setStyleSheet(
            "QCheckBox { color: #ccc; font-size: 12px; }"
        )
        self._auto_clone_cb.toggled.connect(self._on_auto_clone_toggled)
        voice_layout.addWidget(self._auto_clone_cb)

        # Capture row: button + progress bar
        capture_row = QHBoxLayout()
        capture_row.setSpacing(8)

        self._capture_voice_btn = QPushButton("Record Voice")
        self._capture_voice_btn.setMinimumHeight(32)
        self._capture_voice_btn.setToolTip(
            "Record a speaker's voice for cloning (requires active translation)"
        )
        self._capture_voice_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #555; }
            """
        )
        self._capture_voice_btn.clicked.connect(self._on_capture_voice_clicked)
        capture_row.addWidget(self._capture_voice_btn)

        self._import_voice_btn = QPushButton("Import Voice")
        self._import_voice_btn.setMinimumHeight(32)
        self._import_voice_btn.setToolTip(
            "Clone a voice from an audio file (WAV, MP3, etc.)"
        )
        self._import_voice_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #7B1FA2;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover { background-color: #6A1B9A; }
            QPushButton:disabled { background-color: #555; }
            """
        )
        self._import_voice_btn.clicked.connect(self._on_import_voice_clicked)
        capture_row.addWidget(self._import_voice_btn)

        self._clone_progress = QProgressBar()
        self._clone_progress.setMinimum(0)
        self._clone_progress.setMaximum(100)
        self._clone_progress.setValue(0)
        self._clone_progress.setFormat("No capture")
        self._clone_progress.setMinimumHeight(28)
        self._clone_progress.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                text-align: center;
                background: #2a2a2a;
                color: #ccc;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 3px;
            }
            """
        )
        capture_row.addWidget(self._clone_progress, 1)
        voice_layout.addLayout(capture_row)

        # Separator between capture controls and voice library
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #444;")
        voice_layout.addWidget(separator)

        # Voice list header
        list_header = QHBoxLayout()
        header_label = QLabel("Voice Library")
        header_label.setStyleSheet("font-size: 12px; color: #bbb;")
        list_header.addWidget(header_label)
        list_header.addStretch()

        self._voice_count_label = QLabel("0 voices")
        self._voice_count_label.setStyleSheet("font-size: 11px; color: #888;")
        list_header.addWidget(self._voice_count_label)
        voice_layout.addLayout(list_header)

        # Voice list (saved / cached voices)
        self._voice_list = QListWidget()
        self._voice_list.setMinimumHeight(100)
        self._voice_list.setMaximumHeight(200)
        self._voice_list.setAlternatingRowColors(True)
        self._voice_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._voice_list.setStyleSheet(
            """
            QListWidget {
                background-color: #1e1e2e;
                border: 1px solid #444;
                border-radius: 4px;
                outline: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:alternate {
                background-color: #252535;
            }
            QListWidget::item:selected {
                background-color: #1a3a5c;
                color: #e0e0e0;
            }
            QListWidget::item:hover {
                background-color: #2a2a4a;
            }
            """
        )
        self._voice_list.setToolTip(
            "Double-click to activate a voice for TTS"
        )
        self._voice_list.itemDoubleClicked.connect(
            self._on_voice_double_clicked
        )
        voice_layout.addWidget(self._voice_list)

        # Action buttons row
        voice_btn_layout = QHBoxLayout()
        voice_btn_layout.setSpacing(6)

        self._select_voice_btn = QPushButton("Use Voice")
        self._select_voice_btn.setMinimumHeight(28)
        self._select_voice_btn.setToolTip("Set selected voice as active TTS voice")
        self._select_voice_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #555; }
            """
        )
        self._select_voice_btn.clicked.connect(self._on_select_voice_clicked)
        voice_btn_layout.addWidget(self._select_voice_btn)

        self._delete_voice_btn = QPushButton("Delete")
        self._delete_voice_btn.setMinimumHeight(28)
        self._delete_voice_btn.setToolTip("Delete selected voice from cache and ElevenLabs")
        self._delete_voice_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #c62828;
                color: white;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #b71c1c; }
            QPushButton:disabled { background-color: #555; }
            """
        )
        self._delete_voice_btn.clicked.connect(self._on_delete_voice_clicked)
        voice_btn_layout.addWidget(self._delete_voice_btn)

        voice_btn_layout.addStretch()
        voice_layout.addLayout(voice_btn_layout)
        control_layout.addWidget(voice_group)

        # Audio meter
        self._audio_meter = AudioMeter("Input Level")
        control_layout.addWidget(self._audio_meter)

        main_layout.addWidget(control_group)

        # Bottom section: Live output
        output_splitter = QSplitter(Qt.Orientation.Vertical)

        # Transcription box
        transcription_group = QGroupBox("Live Transcription (Original)")
        transcription_layout = QVBoxLayout(transcription_group)
        self._transcription_text = QTextEdit()
        self._transcription_text.setReadOnly(True)
        self._transcription_text.setMaximumHeight(100)
        self._transcription_text.setPlaceholderText(
            "Transcribed text will appear here..."
        )
        transcription_layout.addWidget(self._transcription_text)
        output_splitter.addWidget(transcription_group)

        # Translation box (with pop-out & customization controls)
        self._translation_group = QGroupBox("Live Translation (Dubbed)")
        translation_layout = QVBoxLayout(self._translation_group)

        # Toolbar: font size, text opacity, pop-out
        dubbed_toolbar = QHBoxLayout()
        dubbed_toolbar.setSpacing(6)

        dubbed_toolbar.addWidget(QLabel("Size:"))
        self._dubbed_font_slider = QSlider(Qt.Orientation.Horizontal)
        self._dubbed_font_slider.setRange(8, 48)
        self._dubbed_font_slider.setFixedWidth(80)
        self._dubbed_font_slider.setToolTip("Adjust text font size")
        self._dubbed_font_slider.setValue(self._settings.ui.dubbed_font_size)
        self._dubbed_font_slider.valueChanged.connect(self._on_dubbed_font_changed)
        dubbed_toolbar.addWidget(self._dubbed_font_slider)

        self._dubbed_font_label = QLabel(str(self._settings.ui.dubbed_font_size))
        self._dubbed_font_label.setFixedWidth(24)
        dubbed_toolbar.addWidget(self._dubbed_font_label)

        dubbed_toolbar.addSpacing(8)

        dubbed_toolbar.addWidget(QLabel("Text:"))
        self._dubbed_text_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._dubbed_text_opacity_slider.setRange(20, 100)
        self._dubbed_text_opacity_slider.setFixedWidth(80)
        self._dubbed_text_opacity_slider.setToolTip("Adjust text visibility")
        self._dubbed_text_opacity_slider.setValue(int(self._settings.ui.dubbed_text_opacity * 100))
        self._dubbed_text_opacity_slider.valueChanged.connect(self._on_dubbed_text_opacity_changed)
        dubbed_toolbar.addWidget(self._dubbed_text_opacity_slider)

        self._dubbed_text_opacity_label = QLabel(f"{int(self._settings.ui.dubbed_text_opacity * 100)}%")
        self._dubbed_text_opacity_label.setFixedWidth(36)
        dubbed_toolbar.addWidget(self._dubbed_text_opacity_label)

        dubbed_toolbar.addStretch()

        self._popout_btn = QPushButton("Pop Out")
        self._popout_btn.setToolTip("Detach dubbed text into a floating window (Ctrl+P)")
        self._popout_btn.setFixedWidth(70)
        self._popout_btn.clicked.connect(self._on_popout_clicked)
        dubbed_toolbar.addWidget(self._popout_btn)

        translation_layout.addLayout(dubbed_toolbar)

        self._translation_text = QTextEdit()
        self._translation_text.setReadOnly(True)
        self._translation_text.setMaximumHeight(100)
        self._translation_text.setPlaceholderText(
            "Translated text will appear here..."
        )
        # Apply saved font size
        self._apply_dubbed_font(self._settings.ui.dubbed_font_size)
        translation_layout.addWidget(self._translation_text)
        output_splitter.addWidget(self._translation_group)

        main_layout.addWidget(output_splitter, 1)

        # Detachable dubbed window (created lazily but configured now)
        self._dubbed_window: DubbedWindow | None = None
        self._dubbed_detached = False

        # Status bar
        self._status_bar = StatusBar()
        main_layout.addWidget(self._status_bar)

    def _connect_events(self) -> None:
        """Connect to event bus events."""
        # App lifecycle
        unsub = self._event_bus.subscribe(
            EventType.APP_INITIALIZED, self._on_app_initialized
        )
        self._unsubscribers.append(unsub)

        # Audio events
        unsub = self._event_bus.subscribe(
            EventType.AUDIO_LEVEL_UPDATE, self._on_audio_level
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.AUDIO_SESSION_DETECTED, self._on_session_detected
        )
        self._unsubscribers.append(unsub)

        # Voice clone events
        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_PROGRESS, self._on_clone_progress
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_COMPLETED, self._on_clone_completed
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.VOICE_CLONE_FAILED, self._on_clone_failed
        )
        self._unsubscribers.append(unsub)

        # Translation events
        unsub = self._event_bus.subscribe(
            EventType.TRANSCRIPTION_UPDATE, self._on_transcription
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.TRANSLATION_UPDATE, self._on_translation
        )
        self._unsubscribers.append(unsub)

        # State changes
        unsub = self._event_bus.subscribe(
            EventType.STATE_CHANGED, self._on_state_changed
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.TRANSLATION_STATE_CHANGED,
            self._on_translation_state_changed,
        )
        self._unsubscribers.append(unsub)

        # Errors
        unsub = self._event_bus.subscribe(
            EventType.ERROR_OCCURRED, self._on_error
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.WARNING_OCCURRED, self._on_warning
        )
        self._unsubscribers.append(unsub)

    def _setup_debug_window(self) -> None:
        """Set up the debug window as a dock widget."""
        self._debug_window = DebugWindow(
            orchestrator=self._orchestrator,
            event_bus=self._event_bus,
            parent=self,
        )
        self._debug_window.hide()  # Hidden by default
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self._debug_window
        )

    def _setup_menus(self) -> None:
        """Set up menu bar with Settings, Debug, and Help menus."""
        menu_bar = self.menuBar()
        if menu_bar is None:
            return

        # Settings menu
        settings_menu = menu_bar.addMenu("&Settings")
        if settings_menu is not None:
            api_keys_action = settings_menu.addAction("&API Keys...")
            if api_keys_action is not None:
                api_keys_action.triggered.connect(self._open_settings)

        # Debug menu
        debug_menu = menu_bar.addMenu("&Debug")
        if debug_menu is None:
            return
        self._toggle_debug_action = debug_menu.addAction("Show Debug Monitor")
        if self._toggle_debug_action is not None:
            self._toggle_debug_action.setCheckable(True)
            self._toggle_debug_action.setChecked(False)
            self._toggle_debug_action.triggered.connect(self._toggle_debug_window)
            self._toggle_debug_action.setShortcut("Ctrl+D")

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        if help_menu is not None:
            about_action = help_menu.addAction("&About Live Translate")
            if about_action is not None:
                about_action.triggered.connect(self._show_about_dialog)

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts for common operations."""
        # Ctrl+Enter — Start translation
        start_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        start_shortcut.activated.connect(self._on_start_clicked)
        self._start_btn.setToolTip("Start Translation (Ctrl+Enter)")

        # Ctrl+Shift+S — Stop translation
        stop_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        stop_shortcut.activated.connect(self._on_stop_clicked)
        self._stop_btn.setToolTip("Stop Translation (Ctrl+Shift+S)")

        # Ctrl+P — Toggle pop out / dock
        popout_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        popout_shortcut.activated.connect(self._on_popout_clicked)

    @pyqtSlot(bool)
    def _toggle_debug_window(self, checked: bool) -> None:
        """Toggle debug window visibility."""
        if checked:
            self._debug_window.show()
        else:
            self._debug_window.hide()

    def _open_settings(self) -> None:
        """Open the settings dialog for API key configuration."""
        from live_dubbing.gui.widgets.settings_dialog import SettingsDialog

        dialog = SettingsDialog(settings=self._settings, parent=self)
        dialog.exec()

        if dialog.was_saved:
            # Reinitialize ElevenLabs service with new key
            if self._async_worker:
                self._async_worker.run_coroutine(
                    self._orchestrator.reinit_elevenlabs(),
                )
            # Refresh status bar
            self._refresh_ui()

    def _show_about_dialog(self) -> None:
        """Show the About dialog with credits."""
        QMessageBox.about(
            self,
            "About Live Translate",
            "<h2>Live Translate</h2>"
            "<p>Version 0.1.0</p>"
            "<p>Real-time audio translation and voice-cloned dubbing "
            "for Windows applications.</p>"
            "<hr>"
            "<p><b>Powered by</b></p>"
            "<p>Voice cloning &amp; text-to-speech by "
            '<a href="https://elevenlabs.io">ElevenLabs</a></p>'
            "<p>Speech recognition by "
            '<a href="https://openai.com">OpenAI Whisper</a></p>'
            "<p>Translation by "
            '<a href="https://openai.com">OpenAI GPT</a></p>'
            "<hr>"
            "<p>Licensed under MIT</p>",
        )

    def _setup_refresh_timer(self) -> None:
        """Set up timer for periodic UI refresh."""
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start(1000)  # 1 second

    def _refresh_ui(self) -> None:
        """Periodic UI refresh."""
        # Update status bar
        state = self._orchestrator.get_state_snapshot()
        self._status_bar.set_vb_cable_status(state.vb_cable_installed)
        self._status_bar.set_api_status(state.api_key_configured)

        # Hide API banner once key is configured
        if state.api_key_configured and self._api_banner.isVisible():
            self._api_banner.setVisible(False)

        if state.pipeline_stats:
            self._status_bar.set_latency(
                state.pipeline_stats.current_latency_ms
            )

    @pyqtSlot()
    def _refresh_sessions(self) -> None:
        """Refresh audio sessions list."""
        sessions = self._orchestrator.get_audio_sessions()
        self._app_selector.set_sessions(sessions)

    def _populate_output_devices(self) -> None:
        """Fill output device combo from sounddevice and restore selection."""
        try:
            self._output_device_combo.blockSignals(True)
            self._output_device_combo.clear()

            # Get devices safely (may crash in PortAudio on some systems)
            try:
                devices = get_output_devices()
            except Exception as e:
                logger.warning("Could not query output devices", error=str(e))
                devices = [("", "Default")]

            saved_id = self._settings.audio.output_device_id or ""
            for dev_id, name in devices:
                self._output_device_combo.addItem(name, dev_id)

            # Restore selection
            for i in range(self._output_device_combo.count()):
                if self._output_device_combo.itemData(i) == saved_id:
                    self._output_device_combo.setCurrentIndex(i)
                    break
            self._output_device_combo.blockSignals(False)
        except Exception as e:
            logger.exception("Error populating output devices", error=str(e))
            self._output_device_combo.blockSignals(False)

    @pyqtSlot(int)
    def _on_output_device_changed(self, index: int) -> None:
        """Save selected output device by index.

        Uses deferred execution to avoid potential crashes during signal handling.
        """
        if index < 0:
            return
        # Defer the actual handling to avoid issues during Qt signal emission
        QTimer.singleShot(0, lambda: self._handle_output_device_change(index))

    def _handle_output_device_change(self, index: int) -> None:
        """Actually handle the output device change (deferred from signal)."""
        try:
            if index < 0 or index >= self._output_device_combo.count():
                return
            dev_id: Any = self._output_device_combo.itemData(index)
            self._settings.audio.output_device_id = dev_id if dev_id else None
            try:
                ConfigManager().save(self._settings)
            except Exception as e:
                logger.warning(
                    "Could not save output device setting", error=str(e)
                )
            logger.info("Output device set", device_id=dev_id or "default")
        except Exception as e:
            logger.exception(
                "Error handling output device change", error=str(e)
            )

    @pyqtSlot()
    def _on_start_clicked(self) -> None:
        """Handle start button click."""
        session = self._app_selector.get_selected_session()
        if not session:
            QMessageBox.warning(
                self,
                "No Application Selected",
                "Please select an application to capture audio from.",
            )
            return

        if not self._orchestrator.is_api_key_configured:
            QMessageBox.warning(
                self,
                "API Key Not Configured",
                "Please configure your ElevenLabs API key.\n\n"
                "Go to Settings > API Keys to enter your key.",
            )
            return

        capture_mode = self._capture_mode_combo.currentData()

        if capture_mode == "vbcable":
            # VB-Cable mode: check installation, show routing instructions
            if not self._orchestrator.is_vb_cable_installed:
                self._show_vb_cable_wizard(session)
                return
            self._show_routing_configuration(session)
        else:
            # System audio mode: start immediately, no setup needed
            self._start_translation(session, use_fallback=True)

    def _show_vb_cable_wizard(self, session: AudioSessionInfo) -> None:
        """Show VB-Cable setup wizard."""

        def detect_vb_cable() -> bool:
            """Detect VB-Cable for the wizard."""
            orch = self._orchestrator
            routing = getattr(orch, "_audio_routing", None)
            if routing is not None:
                routing.detect_virtual_devices()
                return bool(routing.is_vb_cable_installed())
            return bool(self._orchestrator.is_vb_cable_installed)

        wizard = VBCableSetupWizard(
            detect_func=detect_vb_cable,
            app_name=session.name if session else "",
            parent=self,
        )

        wizard.setup_complete.connect(
            lambda vb_cable, fallback: self._on_wizard_complete(
                session, vb_cable, fallback
            )
        )

        wizard.exec()

    def _show_routing_configuration(self, session: AudioSessionInfo) -> None:
        """Show routing dialog when VB-Cable is already installed."""
        result = QMessageBox.information(
            self,
            "Configure Audio Routing",
            f"Route '{session.name}' audio to VB-Cable:\n\n"
            "1. Click 'Open Sound Settings' below\n"
            "2. Find your app and change Output to 'CABLE Input'\n\n"
            "Click OK when done, or Cancel to use System Audio instead.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )

        if result == QMessageBox.StandardButton.Cancel:
            # Fall back to system audio
            self._start_translation(session, use_fallback=True)
            return

        # Start translation with VB-Cable
        self._start_translation(session, use_fallback=False)

    def _on_wizard_complete(self, session: AudioSessionInfo, vb_cable_used: bool, fallback_used: bool) -> None:
        """Handle wizard completion."""
        if fallback_used:
            # User chose system loopback fallback
            self._use_system_fallback = True
            self._start_translation(session, use_fallback=True)
        elif vb_cable_used:
            # VB-Cable configured
            self._use_system_fallback = False
            self._start_translation(session, use_fallback=False)
        # If neither, wizard was cancelled

    def _start_translation(self, session: AudioSessionInfo, use_fallback: bool = False) -> None:
        """Start the translation process."""
        target_lang = self._language_panel.get_target_language()
        source_lang = self._language_panel.get_source_language()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._app_selector.set_enabled(False)
        self._language_panel.set_enabled(False)
        self._capture_mode_combo.setEnabled(False)
        self._is_running = True

        # Show locked-state hint on disabled panels
        self._app_selector._info_label.setText(
            "Locked while translating — click Stop to change"
        )
        self._language_panel._info_label.setText(
            "Locked while translating — click Stop to change"
        )

        # Clear output
        self._transcription_text.clear()
        self._translation_text.clear()
        if self._dubbed_window is not None:
            self._dubbed_window.clear_text()
        self._clone_progress.setValue(0)
        self._clone_progress.setFormat("Capturing voice sample...")

        # Start orchestrator via async worker (thread-safe)
        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.start_translation(
                    target_app=session,
                    target_language=target_lang,
                    source_language=source_lang,
                    use_system_fallback=use_fallback,
                ),
                on_error=self._handle_translation_error,
            )
        else:
            logger.error("No async worker available to start translation")
            self._handle_translation_error("No async worker available")

    @pyqtSlot()
    def _on_stop_clicked(self) -> None:
        """Handle stop button click."""
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._app_selector.set_enabled(True)
        self._language_panel.set_enabled(True)
        self._capture_mode_combo.setEnabled(True)
        self._is_running = False

        # Restore info labels
        self._app_selector._update_info_label()
        self._language_panel._on_language_changed()

        # Stop orchestrator via async worker (thread-safe)
        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.stop_translation()
            )
        else:
            logger.error("No async worker available to stop translation")

    def _handle_translation_error(self, error_msg: str) -> None:
        """Handle translation error from async worker."""
        logger.error("Translation failed", error=error_msg)
        self.show_error(f"Translation failed: {error_msg}")

        # Reset UI state
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._app_selector.set_enabled(True)
        self._language_panel.set_enabled(True)
        self._capture_mode_combo.setEnabled(True)
        self._is_running = False
        self._app_selector._update_info_label()
        self._language_panel._on_language_changed()

        # Reset progress bar
        self._clone_progress.setValue(0)
        self._clone_progress.setFormat("Ready")

    @pyqtSlot(object)
    def _on_app_initialized(self, event: Event) -> None:
        """Handle app initialized event."""
        self._refresh_sessions()
        self._status_bar.set_app_state(AppState.READY)

        # Show API banner if key is missing
        has_key = bool(self._settings.get_elevenlabs_api_key())
        self._api_banner.setVisible(not has_key)
        self._status_bar.set_api_status(has_key)

    @pyqtSlot(object)
    def _on_audio_level(self, event: Event) -> None:
        """Handle audio level update."""
        level = event.data.get("level", 0.0)
        is_speech = event.data.get("is_speech", False)
        self._audio_meter.set_level(level, is_speech)

    @pyqtSlot(object)
    def _on_session_detected(self, event: Event) -> None:
        """Handle audio session detected."""
        self._refresh_sessions()

    @pyqtSlot(object)
    def _on_clone_progress(self, event: Event) -> None:
        """Handle voice clone progress."""
        progress = event.data.get("progress", 0.0)
        pct = int(progress * 100)
        self._clone_progress.setValue(pct)
        speaker = event.data.get("speaker_label", "")
        if speaker:
            label = f"Recording {speaker}... keep speaking ({pct}%)"
        else:
            label = f"Recording... keep speaking ({pct}%)"
        self._clone_progress.setFormat(label)

    @pyqtSlot(object)
    def _on_clone_completed(self, event: Event) -> None:
        """Handle voice clone completed."""
        self._clone_progress.setValue(100)
        voice_name = event.data.get("name", "")
        done_label = f"Voice cloned: {voice_name}" if voice_name else "Voice cloned!"
        self._clone_progress.setFormat(done_label)
        self._clone_progress.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                text-align: center;
                background: #2a2a2a;
                color: #ccc;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
            """
        )
        # Re-enable capture/import buttons
        self._capture_voice_btn.setText("Capture Voice")
        self._capture_voice_btn.setEnabled(True)
        self._import_voice_btn.setText("Import Voice")
        self._import_voice_btn.setEnabled(True)
        # Refresh the voice list so the new clone appears
        self._refresh_voice_list()

    @pyqtSlot(object)
    def _on_clone_failed(self, event: Event) -> None:
        """Handle voice clone failure."""
        _error = event.data.get("error", "Unknown error")
        self._clone_progress.setValue(0)
        self._clone_progress.setFormat("Clone failed")
        # Re-enable buttons
        self._capture_voice_btn.setText("Capture Voice")
        self._capture_voice_btn.setEnabled(True)
        self._import_voice_btn.setText("Import Voice")
        self._import_voice_btn.setEnabled(True)

    # ── Voice panel handlers ─────────────────────────────────────────────

    def _refresh_voice_list(self) -> None:
        """Reload the voice list from the orchestrator's cache with rich display."""
        self._voice_list.clear()
        voices = self._orchestrator.get_saved_voices()
        default_id = self._settings.voice_clone.default_voice_id
        active_id = default_id

        for v in voices:
            name = v.speaker_id or v.name
            # Build rich display line
            dur = f"{v.sample_duration_sec:.0f}s" if v.sample_duration_sec else ""
            date = v.created_at.strftime("%b %d") if v.created_at else ""
            is_active = v.voice_id == active_id

            parts = [name]
            if dur:
                parts.append(dur)
            if date:
                parts.append(date)
            detail = "  |  ".join(parts)

            if is_active:
                display = f"\u25B6  {detail}"  # ▶ active marker
            else:
                display = f"    {detail}"

            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, v.voice_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, name)  # store name for later

            if is_active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

            self._voice_list.addItem(item)
            if is_active:
                item.setSelected(True)
                self._voice_list.setCurrentItem(item)

        # Update count label
        count = len(voices)
        self._voice_count_label.setText(
            f"{count} voice{'s' if count != 1 else ''}"
        )

    def _on_voice_double_clicked(self, item: QListWidgetItem) -> None:
        """Activate a voice by double-clicking it in the list."""
        voice_id = item.data(Qt.ItemDataRole.UserRole)
        if voice_id and self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.switch_voice(voice_id),
            )
            name = item.data(Qt.ItemDataRole.UserRole + 1) or "Voice"
            self._clone_progress.setFormat(f"Active: {name}")
            # Refresh to update active marker
            QTimer.singleShot(300, self._refresh_voice_list)

    def _on_auto_clone_toggled(self, checked: bool) -> None:
        """Save auto-clone preference to settings."""
        self._settings.voice_clone.auto_clone_voice = checked
        ConfigManager().save(self._settings)

    def _on_capture_voice_clicked(self) -> None:
        """Start capturing a new speaker's voice."""
        if not self._is_running:
            QMessageBox.information(
                self,
                "Start Translation First",
                "Please start translation before capturing a voice.\n"
                "The capture uses audio from the active stream.",
            )
            return

        name, ok = QInputDialog.getText(
            self,
            "Capture Voice",
            "Enter a name for this speaker:",
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.start_voice_capture(name),
            )
            self._clone_progress.setValue(0)
            self._clone_progress.setFormat(f"Capturing: {name}...")
            # Switch button to indicate capture is in progress
            self._capture_voice_btn.setText("Capturing...")
            self._capture_voice_btn.setEnabled(False)

    def _on_import_voice_clicked(self) -> None:
        """Import a voice from an audio file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audio File for Voice Cloning",
            "",
            "Audio Files (*.wav *.mp3 *.flac *.ogg *.m4a *.aac);;All Files (*)",
        )
        if not file_path:
            return

        name, ok = QInputDialog.getText(
            self,
            "Voice Name",
            "Enter a name for this voice:",
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        self._import_voice_btn.setEnabled(False)
        self._import_voice_btn.setText("Importing...")
        self._clone_progress.setValue(50)
        self._clone_progress.setFormat(f"Importing: {name}...")

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.clone_voice_from_file(file_path, name),
                on_error=self._on_import_voice_error,
            )

    def _on_import_voice_error(self, error_msg: str) -> None:
        """Handle import voice error."""
        self._import_voice_btn.setEnabled(True)
        self._import_voice_btn.setText("Import Voice")
        self._clone_progress.setValue(0)
        self._clone_progress.setFormat("Import failed")
        QMessageBox.critical(
            self, "Import Failed",
            f"Failed to clone voice from file:\n{error_msg}",
        )

    def _on_select_voice_clicked(self) -> None:
        """Set the currently selected voice as active."""
        item = self._voice_list.currentItem()
        if not item:
            QMessageBox.information(
                self, "No Voice Selected",
                "Select a voice from the list first.",
            )
            return
        self._on_voice_double_clicked(item)

    def _on_delete_voice_clicked(self) -> None:
        """Delete the currently selected voice."""
        item = self._voice_list.currentItem()
        if not item:
            QMessageBox.information(
                self, "No Voice Selected",
                "Select a voice from the list first.",
            )
            return

        voice_id = item.data(Qt.ItemDataRole.UserRole)
        name = item.data(Qt.ItemDataRole.UserRole + 1) or "this voice"
        reply = QMessageBox.question(
            self,
            "Delete Voice",
            f'Delete "{name}"?\n\nThis will permanently remove it from '
            f'ElevenLabs and the local cache.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.delete_voice(voice_id),
            )
            # Remove from list immediately and refresh
            row = self._voice_list.row(item)
            self._voice_list.takeItem(row)
            count = self._voice_list.count()
            self._voice_count_label.setText(
                f"{count} voice{'s' if count != 1 else ''}"
            )

    # ── Dubbed window pop-out / customization ────────────────────────────

    def _apply_dubbed_font(self, size: int) -> None:
        """Apply font size to the inline translation text edit."""
        font = QFont()
        font.setPointSize(size)
        self._translation_text.setFont(font)

    def _on_dubbed_font_changed(self, value: int) -> None:
        """Handle font size slider change."""
        self._dubbed_font_label.setText(str(value))
        self._settings.ui.dubbed_font_size = value
        self._apply_dubbed_font(value)
        # Sync to floating window if open
        if self._dubbed_window is not None:
            self._dubbed_window._font_slider.setValue(value)

    def _on_dubbed_text_opacity_changed(self, value: int) -> None:
        """Handle inline text opacity slider change."""
        self._dubbed_text_opacity_label.setText(f"{value}%")
        self._settings.ui.dubbed_text_opacity = value / 100.0
        # Apply to inline text display
        alpha = int(value * 2.55)
        self._translation_text.setStyleSheet(
            f"color: rgba(234, 234, 234, {alpha});"
        )
        # Sync to floating window if open
        if self._dubbed_window is not None:
            self._dubbed_window._text_opacity_slider.setValue(value)

    def _on_popout_clicked(self) -> None:
        """Detach the translation display into a floating window."""
        if self._dubbed_detached and self._dubbed_window is not None:
            # Already detached — just bring to front
            self._dubbed_window.show()
            self._dubbed_window.raise_()
            self._dubbed_window.activateWindow()
            return

        # Create the floating window
        self._dubbed_window = DubbedWindow(
            ui_config=self._settings.ui,
        )
        self._dubbed_window.reattach_requested.connect(self._on_reattach)

        # Copy existing text to the floating window
        existing = self._translation_text.toPlainText()
        if existing.strip():
            self._dubbed_window.append_text(existing)

        # Hide inline translation box
        self._translation_group.hide()
        self._dubbed_detached = True
        self._popout_btn.setText("Show")
        self._popout_btn.setToolTip("Bring the floating window to front")

        self._dubbed_window.show()
        logger.info("Dubbed window detached")

    def _on_reattach(self) -> None:
        """Re-dock the floating window back into the main window."""
        if self._dubbed_window is not None:
            # Sync slider values back
            self._dubbed_font_slider.blockSignals(True)
            self._dubbed_font_slider.setValue(self._dubbed_window.get_font_size())
            self._dubbed_font_slider.blockSignals(False)
            self._dubbed_font_label.setText(str(self._dubbed_window.get_font_size()))
            self._apply_dubbed_font(self._dubbed_window.get_font_size())

            self._dubbed_text_opacity_slider.blockSignals(True)
            self._dubbed_text_opacity_slider.setValue(int(self._dubbed_window.get_text_opacity() * 100))
            self._dubbed_text_opacity_slider.blockSignals(False)
            self._dubbed_text_opacity_label.setText(f"{int(self._dubbed_window.get_text_opacity() * 100)}%")

            self._dubbed_window.hide()
            self._dubbed_window.deleteLater()
            self._dubbed_window = None

        # Show inline translation box again
        self._translation_group.show()
        self._dubbed_detached = False
        self._popout_btn.setText("Pop Out")
        self._popout_btn.setToolTip("Detach dubbed text into a floating window")
        logger.info("Dubbed window re-docked")

    @pyqtSlot(object)
    def _on_transcription(self, event: Event) -> None:
        """Handle transcription update."""
        text = event.data.get("text", "") or ""
        self._transcription_text.append(text)
        scrollbar = self._transcription_text.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    @pyqtSlot(object)
    def _on_translation(self, event: Event) -> None:
        """Handle translation update."""
        text = event.data.get("text", "")
        if text:
            # Always update inline text (hidden when detached, but keeps buffer)
            self._translation_text.append(text)
            scrollbar = self._translation_text.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())

            # Also update detached window if open
            if self._dubbed_detached and self._dubbed_window is not None:
                self._dubbed_window.append_text(text)

    @pyqtSlot(object)
    def _on_state_changed(self, event: Event) -> None:
        """Handle app state change."""
        new_state = event.data.get("new_state")
        if isinstance(new_state, AppState):
            self._status_bar.set_app_state(new_state)

    @pyqtSlot(object)
    def _on_translation_state_changed(self, event: Event) -> None:
        """Handle translation state change."""
        new_state = event.data.get("new_state")
        if isinstance(new_state, TranslationState):
            self._status_bar.set_translation_state(new_state)

    @pyqtSlot(object)
    def _on_error(self, event: Event) -> None:
        """Handle error event."""
        message = event.data.get("message", "Unknown error")
        logger.error("Error occurred", message=message)
        self.show_error(message)

    @pyqtSlot(object)
    def _on_warning(self, event: Event) -> None:
        """Handle warning event."""
        message = event.data.get("message", "Unknown warning")
        logger.warning("Warning", message=message)
        QMessageBox.warning(self, "Warning", message)

    def show_error(self, message: str) -> None:
        """Display error message to user."""
        QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Handle window close event."""
        if self._is_running and self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.stop_translation()
            )
        self._settings.ui.window_x = self.x()
        self._settings.ui.window_y = self.y()
        self._settings.ui.window_width = self.width()
        self._settings.ui.window_height = self.height()

        # Save dubbed window state
        self._settings.ui.dubbed_detached = self._dubbed_detached
        if self._dubbed_window is not None:
            self._dubbed_window._save_geometry()
            self._dubbed_window.close()
            self._dubbed_window = None

        # Persist settings
        try:
            ConfigManager().save(self._settings)
        except Exception as e:
            logger.warning("Could not save settings on close", error=str(e))

        for unsub in self._unsubscribers:
            unsub()
        if event is not None:
            event.accept()
