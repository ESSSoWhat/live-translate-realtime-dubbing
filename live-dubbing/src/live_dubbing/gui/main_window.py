"""Main application window."""  # noqa: D200

# pylint: disable=E0611,W0611,C0415,C0301,C0103,W0212,W0613,W0718,W0201,C0302,C0413,C0412,W1309

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from live_dubbing.app import AsyncWorker
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSlot  # pylint: disable=no-name-in-module
from PyQt6.QtGui import (  # pylint: disable=no-name-in-module
    QAction,
    QCloseEvent,
    QFont,
    QIcon,
    QKeySequence,
    QShortcut,
)

# PyQt6 uses dynamic exports; Pylint cannot resolve them without the runtime env
from PyQt6.QtWidgets import (  # pylint: disable=no-name-in-module
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from live_dubbing import __version__
from live_dubbing.audio.playback import get_output_devices
from live_dubbing.audio.session import AudioSessionInfo
from live_dubbing.config.settings import AppSettings, ConfigManager
from live_dubbing.core.events import Event, EventBus, EventType
from live_dubbing.core.mic_translator import MicTranslator
from live_dubbing.core.orchestrator import Orchestrator
from live_dubbing.core.state import AppState, TranslationState
from live_dubbing.gui.widgets.app_selector import AppSelectorWidget
from live_dubbing.gui.widgets.audio_meter import AudioMeter
from live_dubbing.gui.widgets.debug_window import DebugWindow
from live_dubbing.gui.widgets.dubbed_window import DubbedWindow
from live_dubbing.gui.widgets.language_panel import LanguagePanel
from live_dubbing.gui.widgets.mic_translate_panel import MicTranslateWidget
from live_dubbing.gui.widgets.paypal_dialog import PayPalCheckoutDialog
from live_dubbing.gui.widgets.settings_dialog import SettingsDialog
from live_dubbing.gui.widgets.status_bar import StatusBar
from live_dubbing.gui.widgets.usage_meter import UsageMeterWidget

logger = structlog.get_logger(__name__)


class MainWindow(QMainWindow):
    """
    Main application window for Live Dubbing.

    Layout:
    - Top: App selector and language config
    - Middle: Status display and controls
    - Bottom: Live transcription/translation display
    """

    _usage_meter: UsageMeterWidget

    def __init__(
        self,
        orchestrator: Orchestrator,
        event_bus: EventBus,
        settings: AppSettings,
        async_worker: AsyncWorker | None = None,
        auth_response: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the main window with orchestrator, event bus, and settings."""
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._event_bus = event_bus
        self._settings = settings
        self._async_worker = async_worker
        self._auth_response: dict = auth_response or {}

        self._is_running = False
        self._unsubscribers: list = []
        self._use_system_fallback = False  # Track if using system loopback
        self._dubbed_window: DubbedWindow | None = None
        self._dubbed_detached = self._settings.ui.dubbed_window_detached
        self._main_hidden_for_overlay = False
        self._force_quit = False
        self._tray: QSystemTrayIcon | None = None
        self._overlay_clone_timer: QTimer | None = None
        self._overlay_clone_pending = False

        # Usage meter created here so mypy sees the attribute; _setup_ui() adds it to layout
        self._usage_meter: UsageMeterWidget = UsageMeterWidget(self._settings)

        # Mic translator for embedded Mic Translate widget (created before _setup_ui)
        self._mic_translator = MicTranslator(
            settings=self._settings,
            event_bus=self._event_bus,
        )

        self._setup_window()
        self._setup_ui()
        self._setup_debug_window()
        self._setup_mic_translate_panel()
        self._setup_menus()
        self._setup_shortcuts()
        self._setup_tray()
        self._connect_events()
        self._setup_refresh_timer()

        # Populate saved voices / profiles on startup
        self._refresh_voice_list()
        self._refresh_profile_list()

        # Kick off usage meter polling (token is valid by this point in normal flow)
        tier = self._auth_response.get("tier", "free")
        meter: UsageMeterWidget = self._usage_meter  # type: ignore[has-type]
        meter.set_tier(tier)
        # Pre-populate display from login snapshot if available
        login_usage = self._auth_response.get("usage")
        if login_usage and isinstance(login_usage, dict):
            meter._on_usage_fetched(login_usage)
        meter.start_auto_refresh()

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
        root_layout = QVBoxLayout(central_widget)
        root_layout.setSpacing(0)
        root_layout.setContentsMargins(0, 0, 0, 0)

        # Scrollable content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        main_layout = QVBoxLayout(scroll_content)
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
        banner_label = QLabel(
            "API key not configured — sign in or set ELEVENLABS_API_KEY in your environment."
        )
        banner_label.setStyleSheet("color: #ffcc00; font-size: 12px;")
        banner_layout.addWidget(banner_label)
        banner_layout.addStretch()
        self._api_banner.setVisible(False)  # Hidden until we know key is missing
        main_layout.addWidget(self._api_banner)

        # Audio device section
        device_group = QGroupBox("Audio Devices")
        device_row = QHBoxLayout(device_group)

        # Capture mode selector (first item updated in _on_app_initialized)
        device_row.addWidget(QLabel("Capture:"))
        self._capture_mode_combo = QComboBox()
        self._capture_mode_combo.addItem("Selected app only", "vbcable")
        self._capture_mode_combo.addItem(
            "All system audio",
            "system",
        )
        self._capture_mode_combo.setCurrentIndex(1)  # Default system; updated in _on_app_initialized
        self._capture_mode_combo.setMinimumWidth(180)
        self._capture_mode_combo.setMaxVisibleItems(5)
        device_row.addWidget(self._capture_mode_combo)

        device_row.addWidget(QLabel("Channel:"))
        self._capture_channel_combo = QComboBox()
        self._capture_channel_combo.setMinimumWidth(180)
        self._capture_channel_combo.setMaxVisibleItems(12)
        self._capture_channel_combo.setToolTip(
            "Process from Windows volume mixer to capture. "
            "Select an app to capture only that app, or All for system audio."
        )
        self._populate_capture_channels()
        self._capture_channel_combo.activated.connect(self._on_capture_channel_changed)
        device_row.addWidget(self._capture_channel_combo)

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

        self._refresh_devices_btn = QPushButton("Refresh")
        self._refresh_devices_btn.setFixedWidth(60)
        self._refresh_devices_btn.setToolTip("Refresh audio device lists")
        self._refresh_devices_btn.clicked.connect(self._refresh_audio_device_lists)
        device_row.addWidget(self._refresh_devices_btn)

        # Output volume slider
        device_row.addSpacing(15)
        device_row.addWidget(QLabel("Volume:"))
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setMinimum(0)
        self._volume_slider.setMaximum(100)
        self._volume_slider.setValue(int(self._settings.audio.output_volume * 100))
        self._volume_slider.setMinimumWidth(80)
        self._volume_slider.setMaximumWidth(120)
        self._volume_slider.setToolTip("TTS output volume (0–100%)")
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        device_row.addWidget(self._volume_slider)
        self._volume_label = QLabel(f"{int(self._settings.audio.output_volume * 100)}%")
        self._volume_label.setMinimumWidth(32)
        self._volume_label.setStyleSheet("color: #888; font-size: 11px;")
        device_row.addWidget(self._volume_label)

        self._mute_cb = QCheckBox("Mute")
        self._mute_cb.setToolTip("Mute TTS output temporarily")
        self._mute_cb.setStyleSheet("QCheckBox { color: #888; font-size: 11px; }")
        self._mute_cb.toggled.connect(self._on_mute_toggled)
        device_row.addWidget(self._mute_cb)

        self._play_as_mic_cb = QCheckBox("Play as microphone")
        self._play_as_mic_cb.setToolTip(
            "Route TTS to CABLE Input so Zoom/Discord can use it as mic input"
        )
        self._play_as_mic_cb.setStyleSheet("QCheckBox { color: #888; font-size: 11px; }")
        self._play_as_mic_cb.setChecked(self._settings.audio.output_play_as_mic)
        self._play_as_mic_cb.toggled.connect(self._on_play_as_mic_toggled)
        device_row.addWidget(self._play_as_mic_cb)

        self._play_as_mic_hint = QLabel(
            "Set your mic in Zoom/Discord to 'CABLE Output (VB-Audio Virtual Cable)'"
        )
        self._play_as_mic_hint.setStyleSheet("color: #888; font-size: 10px;")
        self._play_as_mic_hint.setVisible(False)
        device_row.addWidget(self._play_as_mic_hint)

        device_row.addStretch()

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

        self._rename_voice_btn = QPushButton("Rename")
        self._rename_voice_btn.setMinimumHeight(28)
        self._rename_voice_btn.setToolTip("Rename selected voice in library")
        self._rename_voice_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #555;
                color: white;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #666; }
            QPushButton:disabled { background-color: #555; }
            """
        )
        self._rename_voice_btn.clicked.connect(self._on_rename_voice_clicked)
        voice_btn_layout.addWidget(self._rename_voice_btn)

        voice_btn_layout.addStretch()
        voice_layout.addLayout(voice_btn_layout)

        # ── Voice profiles (speaker → TTS clone) ─────────────────────────
        profiles_label_row = QHBoxLayout()
        profiles_title = QLabel("Voice Profiles")
        profiles_title.setStyleSheet("font-weight: bold; color: #bbb;")
        profiles_label_row.addWidget(profiles_title)
        self._profile_count_label = QLabel("0 profiles")
        self._profile_count_label.setStyleSheet("color: #888; font-size: 11px;")
        profiles_label_row.addStretch()
        profiles_label_row.addWidget(self._profile_count_label)
        voice_layout.addLayout(profiles_label_row)

        self._profile_list = QListWidget()
        self._profile_list.setMinimumHeight(80)
        self._profile_list.setMaximumHeight(140)
        self._profile_list.setAlternatingRowColors(True)
        self._profile_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._profile_list.setStyleSheet(self._voice_list.styleSheet())
        self._profile_list.setToolTip(
            "Detected speakers. Assign a clone for TTS; unmatched speech uses the default profile."
        )
        self._profile_list.currentItemChanged.connect(self._on_profile_selection_changed)
        voice_layout.addWidget(self._profile_list)

        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("Default:"))
        self._default_profile_combo = QComboBox()
        self._default_profile_combo.setMinimumWidth(140)
        self._default_profile_combo.setToolTip(
            "Used for TTS when the speaker cannot be identified"
        )
        self._default_profile_combo.currentIndexChanged.connect(
            self._on_default_profile_changed
        )
        default_row.addWidget(self._default_profile_combo, 1)
        voice_layout.addLayout(default_row)

        assign_row = QHBoxLayout()
        assign_row.addWidget(QLabel("Clone:"))
        self._profile_voice_combo = QComboBox()
        self._profile_voice_combo.setMinimumWidth(140)
        self._profile_voice_combo.setToolTip("Cloned voice assigned to the selected profile")
        self._profile_voice_combo.currentIndexChanged.connect(
            self._on_profile_voice_assigned
        )
        assign_row.addWidget(self._profile_voice_combo, 1)
        voice_layout.addLayout(assign_row)

        profile_btn_row = QHBoxLayout()
        profile_btn_row.setSpacing(6)
        self._rename_profile_btn = QPushButton("Rename")
        self._rename_profile_btn.setMinimumHeight(28)
        self._rename_profile_btn.setToolTip("Rename selected profile")
        self._rename_profile_btn.clicked.connect(self._on_rename_profile_clicked)
        profile_btn_row.addWidget(self._rename_profile_btn)

        self._delete_profile_btn = QPushButton("Delete")
        self._delete_profile_btn.setMinimumHeight(28)
        self._delete_profile_btn.setToolTip("Delete selected profile")
        self._delete_profile_btn.setStyleSheet(
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
        self._delete_profile_btn.clicked.connect(self._on_delete_profile_clicked)
        profile_btn_row.addWidget(self._delete_profile_btn)
        profile_btn_row.addStretch()
        self._active_profile_label = QLabel("")
        self._active_profile_label.setStyleSheet("color: #81C784; font-size: 11px;")
        profile_btn_row.addWidget(self._active_profile_label)
        voice_layout.addLayout(profile_btn_row)

        control_layout.addWidget(voice_group)

        # Audio meter
        self._audio_meter = AudioMeter("Input Level")
        control_layout.addWidget(self._audio_meter)

        # Translation source: App Audio | Microphone (integrated)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source:"))
        self._source_combo = QComboBox()
        self._source_combo.addItem("Live Translate", "app")
        self._source_combo.addItem("Mic Translate", "mic")
        self._source_combo.setMinimumWidth(140)
        self._source_combo.setToolTip(
            "Live Translate: capture the selected app and show an overlay HUD.\n"
            "Mic Translate: speak into your mic; captions stay in this window."
        )
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self._source_combo)
        source_row.addStretch()
        self._mic_detach_btn = QPushButton("Detach Mic")
        self._mic_detach_btn.setToolTip("Open Mic Translate in a separate dock window")
        self._mic_detach_btn.setFixedWidth(80)
        self._mic_detach_btn.clicked.connect(self._on_mic_detach_clicked)
        self._mic_detach_btn.setVisible(False)  # shown when Mic source selected
        source_row.addWidget(self._mic_detach_btn)
        main_layout.addLayout(source_row)

        # Stacked content: App Audio (device+controls) | Microphone (mic translate)
        self._translation_stack = QStackedWidget()
        app_page = QWidget()
        app_page_layout = QVBoxLayout(app_page)
        app_page_layout.setContentsMargins(0, 0, 0, 0)
        app_page_layout.addWidget(device_group)
        app_page_layout.addWidget(control_group)
        self._translation_stack.addWidget(app_page)

        self._mic_translate_widget = MicTranslateWidget(
            mic_translator=self._mic_translator,
            orchestrator=self._orchestrator,
            event_bus=self._event_bus,
            settings=self._settings,
            async_worker=self._async_worker,
            parent=self,
        )
        self._translation_stack.addWidget(self._mic_translate_widget)
        self._mic_dock: QDockWidget | None = None
        self._mic_placeholder: QWidget | None = None
        self._mic_reattaching = False
        main_layout.addWidget(self._translation_stack)

        # Bottom section: Live output
        output_splitter = QSplitter(Qt.Orientation.Vertical)

        # Transcription box
        transcription_group = QGroupBox("Live Transcription (Original)")
        transcription_group.setMinimumHeight(80)
        transcription_layout = QVBoxLayout(transcription_group)
        transcription_header = QHBoxLayout()
        transcription_header.addStretch()
        self._clear_transcription_btn = QPushButton("Clear")
        self._clear_transcription_btn.setFixedWidth(50)
        self._clear_transcription_btn.setToolTip("Clear transcription text")
        self._clear_transcription_btn.clicked.connect(
            lambda: self._transcription_text.clear()
        )
        transcription_header.addWidget(self._clear_transcription_btn)
        transcription_layout.addLayout(transcription_header)
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

        self._clear_translation_btn = QPushButton("Clear")
        self._clear_translation_btn.setFixedWidth(50)
        self._clear_translation_btn.setToolTip("Clear translation text")
        self._clear_translation_btn.clicked.connect(self._on_clear_translation)
        dubbed_toolbar.addWidget(self._clear_translation_btn)

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
        # _dubbed_window and _dubbed_detached set in __init__
        # Usage meter — quota progress + Upgrade button (widget created in __init__)
        self._usage_meter.upgrade_requested.connect(self._on_upgrade_requested)
        main_layout.addWidget(self._usage_meter)

        scroll_area.setWidget(scroll_content)
        root_layout.addWidget(scroll_area, 1)

        # Status bar (fixed at bottom)
        self._status_bar = StatusBar()
        root_layout.addWidget(self._status_bar)

    def _on_source_changed(self, index: int) -> None:
        """Switch between App Audio and Microphone translation source."""
        self._translation_stack.setCurrentIndex(index)
        self._mic_detach_btn.setVisible(index == 1)
        # Stop the other mode when switching
        if index == 0 and self._mic_translator.is_running and self._async_worker:
            self._async_worker.run_coroutine(self._mic_translator.stop())
        elif index == 1 and self._is_running:
            self._on_stop_clicked()

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

        unsub = self._event_bus.subscribe(
            EventType.VOICE_PROFILE_CHANGED, self._on_voice_profile_changed
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

        unsub = self._event_bus.subscribe(
            EventType.TTS_STARTED, self._on_tts_started
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

        unsub = self._event_bus.subscribe(
            EventType.PROCESS_LOOPBACK_FAILED,
            self._on_process_loopback_failed,
        )
        self._unsubscribers.append(unsub)

        unsub = self._event_bus.subscribe(
            EventType.AUTH_EXPIRED,
            self._on_auth_expired,
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

    def _setup_mic_translate_panel(self) -> None:
        """Mic Translate is embedded in the main UI; no dock widget."""
        # _mic_translator and _mic_translate_widget are created in __init__ / _setup_ui
        pass

    def _setup_menus(self) -> None:
        """Set up menu bar with Account, Debug, Tools, and Help menus."""
        menu_bar = self.menuBar()
        if menu_bar is None:
            return

        # Account menu
        account_menu = menu_bar.addMenu("&Account")
        if account_menu is not None:
            portal_action = account_menu.addAction("Manage Subscription…")
            if portal_action is not None:
                portal_action.triggered.connect(self._open_account_portal)
            paypal_action = account_menu.addAction("Upgrade with PayPal…")
            if paypal_action is not None:
                paypal_action.triggered.connect(self._open_paypal_checkout)
            account_web_action = account_menu.addAction("Manage account on web")
            if account_web_action is not None:
                account_web_action.triggered.connect(self._open_account_on_web)  # type: ignore[attr-defined]
            account_menu.addSeparator()
            sign_out_action = account_menu.addAction("Sign Out")
            if sign_out_action is not None:
                sign_out_action.triggered.connect(self._on_sign_out)

        # Tools menu
        tools_menu = menu_bar.addMenu("&Tools")
        if tools_menu is not None:
            refresh_action = tools_menu.addAction("&Refresh Audio Devices")
            if refresh_action is not None:
                refresh_action.triggered.connect(self._refresh_audio_device_lists)
            tools_menu.addSeparator()
            settings_action = tools_menu.addAction("&Settings…")
            if settings_action is not None:
                settings_action.triggered.connect(self._open_settings)

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
            website_action = help_menu.addAction("Open &Website")
            if website_action is not None:
                website_action.triggered.connect(self._open_website)
            download_action = help_menu.addAction("&Download Live Translate")
            if download_action is not None:
                download_action.triggered.connect(self._open_download)
            help_menu.addSeparator()
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

        # Ctrl+P — Toggle pop out / overlay HUD
        popout_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        popout_shortcut.activated.connect(self._on_popout_clicked)

    def _window_icon(self) -> QIcon:
        """Return the app icon if the asset exists, otherwise an empty icon."""
        import pathlib

        icon_path = pathlib.Path(__file__).parent / "assets" / "logo.png"
        if icon_path.exists():
            return QIcon(str(icon_path))
        return self.windowIcon()

    def _setup_tray(self) -> None:
        """Create a system tray icon for restoring the hidden main window."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("System tray is not available")
            return
        self._tray = QSystemTrayIcon(self._window_icon(), self)
        self._tray.setToolTip("Live Translate")
        menu = QMenu(self)
        show_action = QAction("Show", self)
        show_action.triggered.connect(self._restore_main_window)
        menu.addAction(show_action)
        stop_action = QAction("Stop", self)
        stop_action.triggered.connect(self._on_stop_clicked)
        menu.addAction(stop_action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Restore the main window on a tray click."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_main_window()

    def _quit_from_tray(self) -> None:
        """Exit fully from the tray menu."""
        self._force_quit = True
        self.close()

    def _is_live_source(self) -> bool:
        """True when Live Translate (app audio) is selected."""
        return self._source_combo.currentData() == "app"

    def _overlay_voice_items(self) -> list[tuple[str, str]]:
        """Voice list for the overlay combo: (id, name)."""
        items: list[tuple[str, str]] = []
        for voice in self._orchestrator.get_saved_voices():
            name = voice.speaker_id or voice.name or voice.voice_id
            items.append((voice.voice_id, name))
        return items

    def _ensure_overlay(self) -> DubbedWindow:
        """Create the overlay HUD if needed and connect its signals once."""
        if self._dubbed_window is not None:
            return self._dubbed_window
        overlay = DubbedWindow(ui_config=self._settings.ui)
        overlay.reattach_requested.connect(self._on_reattach)
        overlay.stop_requested.connect(self._on_stop_clicked)
        overlay.mute_toggled.connect(self._on_overlay_mute)
        overlay.volume_changed.connect(self._on_overlay_volume)
        overlay.voice_selected.connect(self._on_overlay_voice)
        overlay.clone_requested.connect(self._on_overlay_clone)
        overlay.home_requested.connect(self._restore_main_window)
        self._dubbed_window = overlay
        self._sync_overlay_state()
        return overlay

    def _sync_overlay_state(self) -> None:
        """Push mute, volume, voices, and caption style into the overlay."""
        overlay = self._dubbed_window
        if overlay is None:
            return
        overlay.set_muted(self._mute_cb.isChecked())
        overlay.set_volume(self._volume_slider.value() / 100.0)
        overlay.set_voices(
            self._overlay_voice_items(),
            self._settings.voice_clone.default_voice_id,
        )
        overlay.set_font_size(self._settings.ui.dubbed_font_size)
        overlay.set_text_opacity(self._settings.ui.dubbed_text_opacity)
        overlay.set_opacity(self._settings.ui.dubbed_opacity)
        overlay.set_session_active(self._is_running)

    def _show_live_overlay(self, hide_main: bool) -> None:
        """Show the overlay HUD; optionally hide the main window."""
        overlay = self._ensure_overlay()
        existing = self._translation_text.toPlainText()
        source = self._transcription_text.toPlainText()
        overlay.clear_text()
        if source.strip():
            overlay.append_source_text(source)
        if existing.strip():
            overlay.append_text(existing)
        self._sync_overlay_state()
        if hide_main:
            overlay.collapse()
        else:
            overlay.expand()
        overlay.show()
        overlay.raise_()
        self._dubbed_detached = True
        self._popout_btn.setText("Show")
        self._popout_btn.setToolTip("Bring the overlay HUD to front")
        if hide_main:
            self._main_hidden_for_overlay = True
            self.hide()
            if self._tray is not None:
                self._tray.showMessage(
                    "Live Translate",
                    "Translation is running in the overlay. "
                    "Click the tray icon or Home to return here.",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )

    def _restore_main_window(self) -> None:
        """Show and focus the main window without stopping translation."""
        self._main_hidden_for_overlay = False
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _hide_live_overlay(self) -> None:
        """Hide the overlay HUD after a live session ends."""
        if self._dubbed_window is not None:
            self._dubbed_window.set_session_active(False)
            self._dubbed_window.set_cloning(False)
            self._dubbed_window.hide()
        self._dubbed_detached = False
        self._popout_btn.setText("Pop Out")
        self._popout_btn.setToolTip("Detach dubbed text into a floating overlay")
        self._translation_group.show()

    def _on_overlay_mute(self, muted: bool) -> None:
        """Apply mute from the overlay HUD."""
        self._mute_cb.blockSignals(True)
        self._mute_cb.setChecked(muted)
        self._mute_cb.blockSignals(False)
        self._on_mute_toggled(muted)

    def _on_overlay_volume(self, volume: float) -> None:
        """Apply volume from the overlay HUD."""
        pct = int(max(0.0, min(1.0, volume)) * 100)
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(pct)
        self._volume_slider.blockSignals(False)
        self._on_volume_changed(pct)

    def _on_overlay_voice(self, voice_id: str) -> None:
        """Switch TTS voice from the overlay HUD."""
        if not voice_id or not self._async_worker:
            return
        self._async_worker.run_coroutine(self._orchestrator.switch_voice(voice_id))

    def _on_overlay_clone(self) -> None:
        """Capture live speech until the clone buffer is full, matching mobile Clone."""
        if not self._is_running or self._overlay_clone_pending:
            return
        self._overlay_clone_pending = True
        if self._dubbed_window is not None:
            self._dubbed_window.set_cloning(True)
            self._dubbed_window.set_status("Recording speech…")
        name = datetime.now().strftime("Clone %H:%M")
        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.start_voice_capture(name),
                on_error=self._on_overlay_clone_error,
            )
        duration_sec = self._settings.voice_clone.dynamic_capture_duration_sec
        timeout_ms = int(max(30.0, duration_sec * 8) * 1000)
        self._overlay_clone_timer = QTimer(self)
        self._overlay_clone_timer.setSingleShot(True)
        self._overlay_clone_timer.timeout.connect(self._on_overlay_clone_timeout)
        self._overlay_clone_timer.start(timeout_ms)

    def _clear_overlay_clone_pending(self) -> None:
        """Stop waiting for an overlay clone result."""
        self._overlay_clone_pending = False
        if self._overlay_clone_timer is not None:
            self._overlay_clone_timer.stop()
            self._overlay_clone_timer = None

    def _on_overlay_clone_timeout(self) -> None:
        """Give up if not enough speech arrived before the wait expired."""
        self._overlay_clone_timer = None
        if not self._overlay_clone_pending:
            return
        self._overlay_clone_pending = False
        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.cancel_voice_capture(),
                on_error=self._on_overlay_clone_error,
            )
        if self._dubbed_window is not None:
            self._dubbed_window.set_cloning(False)
            self._dubbed_window.set_status("Need more speech — try Clone again")
        logger.warning("Overlay clone timed out before enough speech was captured")

    def _on_overlay_clone_error(self, error_msg: str) -> None:
        """Reset overlay clone UI after a failure."""
        self._clear_overlay_clone_pending()
        if self._dubbed_window is not None:
            self._dubbed_window.set_cloning(False)
            self._dubbed_window.set_status("Clone failed")
        logger.warning("Overlay clone failed", error=error_msg)

    @pyqtSlot(bool)
    def _toggle_debug_window(self, checked: bool) -> None:
        """Toggle debug window visibility."""
        if checked:
            self._debug_window.show()
        else:
            self._debug_window.hide()

    def _on_mic_detach_clicked(self) -> None:
        """Detach Mic Translate into a separate dock window."""
        if self._mic_dock is not None and self._mic_dock.isVisible():
            return
        if self._mic_dock is None:
            self._mic_dock = QDockWidget("Mic Translate", self)
            self._mic_dock.setObjectName("MicTranslateDock")
            self._mic_dock.setAllowedAreas(
                Qt.DockWidgetArea.BottomDockWidgetArea
                | Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self._mic_dock.setMinimumSize(360, 420)
            self._mic_dock.visibilityChanged.connect(self._on_mic_dock_visibility_changed)
        self._translation_stack.removeWidget(self._mic_translate_widget)
        self._mic_dock.setWidget(self._mic_translate_widget)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._mic_dock)
        self._mic_dock.show()
        self._mic_placeholder = QWidget()
        place_layout = QVBoxLayout(self._mic_placeholder)
        place_layout.addWidget(QLabel("Mic Translate is in a separate window."))
        reattach_btn = QPushButton("Re-attach")
        reattach_btn.setToolTip("Move Mic Translate back into the main window")
        reattach_btn.clicked.connect(self._on_mic_reattach_clicked)
        place_layout.addWidget(reattach_btn)
        self._translation_stack.addWidget(self._mic_placeholder)
        self._mic_detach_btn.setEnabled(False)

    def _on_mic_dock_visibility_changed(self, visible: bool) -> None:
        """When dock is closed, re-attach the Mic Translate widget."""
        if getattr(self, "_mic_reattaching", False):
            return
        if not visible and self._mic_dock is not None and self._mic_dock.widget() is not None:
            self._on_mic_reattach_clicked()

    def _on_mic_reattach_clicked(self) -> None:
        """Re-attach Mic Translate back into the main window."""
        if self._mic_dock is None or self._mic_placeholder is None:
            return
        if self._mic_reattaching:
            return
        self._mic_reattaching = True
        try:
            try:
                self._mic_dock.visibilityChanged.disconnect(
                    self._on_mic_dock_visibility_changed
                )
            except TypeError:
                pass
            widget = self._mic_dock.widget()
            if widget is not None:
                self._mic_dock.setWidget(None)
                self._translation_stack.removeWidget(self._mic_placeholder)
                self._mic_placeholder.deleteLater()
                self._mic_placeholder = None
                self._translation_stack.insertWidget(1, widget)
            self._mic_dock.hide()
            self.removeDockWidget(self._mic_dock)
            self._mic_detach_btn.setEnabled(True)
        finally:
            self._mic_reattaching = False
            if self._mic_dock is not None:
                try:
                    self._mic_dock.visibilityChanged.connect(
                        self._on_mic_dock_visibility_changed
                    )
                except TypeError:
                    pass

    def _show_about_dialog(self) -> None:
        """Show the About dialog with credits."""
        website_url = self._settings.get_website_url()
        download_url = self._settings.get_download_url()
        QMessageBox.about(
            self,
            "About Live Translate",
            "<h2>Live Translate</h2>"
            f"<p>Version {__version__}</p>"
            "<p>Real-time audio translation and voice-cloned dubbing "
            "for Windows applications.</p>"
            "<p>Official website: "
            f'<a href="{website_url}">{website_url}</a></p>'
            "<p>Download: "
            f'<a href="{download_url}">Get the latest version</a></p>'
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

    def _populate_capture_channels(self) -> None:
        """Fill capture channel combo from Windows volume mixer processes.

        Per-app options require process loopback (Windows 10 build 20348+ or Windows 11).
        When unavailable, only 'All system audio' is shown.
        """
        try:
            self._capture_channel_combo.blockSignals(True)
            self._capture_channel_combo.clear()
            self._capture_channel_combo.addItem("All system audio", None)
            if self._orchestrator.is_process_loopback_supported:
                sessions = self._orchestrator.get_audio_sessions()
                for session in sessions:
                    label = session.name
                    if session.is_muted:
                        label += " (Muted)"
                    self._capture_channel_combo.addItem(label, session)
            saved_pid = self._settings.audio.capture_device_id
            if saved_pid and self._capture_channel_combo.count() > 1:
                try:
                    pid = int(saved_pid)
                    for i in range(self._capture_channel_combo.count()):
                        s = self._capture_channel_combo.itemData(i)
                        if hasattr(s, "pid") and s.pid == pid:
                            self._capture_channel_combo.setCurrentIndex(i)
                            break
                except ValueError:
                    pass
            plb = self._orchestrator.is_process_loopback_supported
            self._capture_channel_combo.setToolTip(
                "Process from Windows volume mixer to capture. "
                + (
                    "Select an app to capture only that app, or All for system audio."
                    if plb
                    else "Per-app capture requires Windows 11 or Server 2022. "
                    "Only 'All system audio' is available on this system."
                )
            )
            self._capture_channel_combo.blockSignals(False)
        except Exception as e:
            logger.warning("Could not populate capture channels", error=str(e))
            self._capture_channel_combo.blockSignals(False)

    def _refresh_audio_device_lists(self) -> None:
        """Refresh both output and capture channel lists."""
        self._populate_output_devices()
        self._populate_capture_channels()

    @pyqtSlot(int)
    def _on_capture_channel_changed(self, index: int) -> None:
        """Save selected capture channel (volume mixer process)."""
        if index < 0:
            return
        data = self._capture_channel_combo.itemData(index)
        if data is None:
            self._settings.audio.capture_device_id = None
        elif hasattr(data, "pid"):
            self._settings.audio.capture_device_id = str(data.pid)
        else:
            self._settings.audio.capture_device_id = None
        try:
            ConfigManager().save(self._settings)
        except Exception as e:
            logger.warning("Could not save capture channel", error=str(e))

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

    @pyqtSlot(bool)
    def _on_play_as_mic_toggled(self, checked: bool) -> None:
        """Save play-as-mic setting and update UI."""
        self._settings.audio.output_play_as_mic = checked
        try:
            ConfigManager().save(self._settings)
        except Exception as e:
            logger.warning("Could not save play-as-mic setting", error=str(e))
        self._update_play_as_mic_ui()

    def _update_play_as_mic_ui(self) -> None:
        """Enable/disable play-as-mic based on VB-Cable; show helper text when on."""
        has_cable = self._orchestrator.is_vb_cable_installed if self._orchestrator else False
        checked = self._play_as_mic_cb.isChecked()
        self._play_as_mic_cb.setEnabled(has_cable)
        if not has_cable:
            self._play_as_mic_cb.setToolTip("Install VB-Cable first (see Audio Routing Setup)")
        else:
            self._play_as_mic_cb.setToolTip(
                "Route TTS to CABLE Input so Zoom/Discord can use it as mic input"
            )
        self._play_as_mic_hint.setVisible(checked and has_cable)

    def _on_volume_changed(self, value: int) -> None:
        """Update output volume from slider and persist."""
        if self._mute_cb.isChecked():
            return
        volume = value / 100.0
        self._volume_label.setText(f"{value}%")
        self._orchestrator.set_output_volume(volume)
        self._settings.audio.output_volume = volume
        if self._dubbed_window is not None:
            self._dubbed_window.set_volume(volume)
        try:
            ConfigManager().save(self._settings)
        except Exception as e:
            logger.warning("Could not save volume setting", error=str(e))

    def _on_mute_toggled(self, checked: bool) -> None:
        """Mute or unmute TTS output."""
        if checked:
            self._orchestrator.set_output_volume(0.0)
        else:
            vol = self._volume_slider.value() / 100.0
            self._orchestrator.set_output_volume(vol)
        if self._dubbed_window is not None:
            self._dubbed_window.set_muted(checked)

    def _on_clear_translation(self) -> None:
        """Clear translation text in main view and overlay HUD."""
        self._translation_text.clear()
        self._transcription_text.clear()
        if self._dubbed_window is not None:
            self._dubbed_window.clear_text()

    def _open_settings(self) -> None:
        """Open the settings dialog."""
        dialog = SettingsDialog(self._settings, self)
        dialog.exec()
        if dialog.was_saved and self._async_worker:
            self._async_worker.run_coroutine(self._orchestrator.reinit_elevenlabs())

    def _open_paypal_checkout(self) -> None:
        """Open the PayPal upgrade dialog, prefilling the signed-in email if known."""
        email = ""
        try:
            email = str(self._auth_response.get("email") or "")
        except Exception:
            email = ""
        dialog = PayPalCheckoutDialog(self._settings, email=email, parent=self)
        dialog.exec()
        # Refresh usage shortly after so a new tier shows up.
        QTimer.singleShot(3000, self._usage_meter.refresh)

    @pyqtSlot()
    def _on_start_clicked(self) -> None:
        """Handle start button click. Capture source is derived from Channel (volume mixer)."""
        channel_data = self._capture_channel_combo.currentData()
        if channel_data is None:
            # "All system audio" — system loopback
            session = AudioSessionInfo(pid=0, name="System")
            use_fallback = True
        elif hasattr(channel_data, "pid"):
            # Specific app from volume mixer
            session = channel_data
            use_fallback = False
        else:
            QMessageBox.warning(
                self,
                "No Channel Selected",
                "Please select a channel (All system audio or an app).",
            )
            return

        if not self._orchestrator.is_api_key_configured:
            QMessageBox.warning(
                self,
                "API Key Not Configured",
                "Please sign in or set ELEVENLABS_API_KEY in your environment.",
            )
            return

        # Client-side quota pre-check (backend also enforces via HTTP 402)
        if self._settings.is_token_valid() and self._usage_meter.is_quota_exceeded():
            import webbrowser  # noqa: PLC0415
            QMessageBox.warning(
                self,
                "Monthly Quota Exhausted",
                "You have used all your dubbing minutes for this month.\n\n"
                "Opening the upgrade page in your browser…",
            )
            webbrowser.open(
                self._usage_meter._checkout_url or self._settings.get_upgrade_url()
            )
            return

        self._start_translation(session, use_fallback=use_fallback)

    def _start_translation(self, session: AudioSessionInfo, use_fallback: bool = False) -> None:
        """Start the translation process."""
        target_lang = self._language_panel.get_target_language()
        source_lang = self._language_panel.get_source_language()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._app_selector.set_enabled(False)
        self._language_panel.set_enabled(False)
        self._capture_mode_combo.setEnabled(False)
        self._capture_channel_combo.setEnabled(False)
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
            return

        if self._is_live_source():
            self._show_live_overlay(hide_main=True)

    @pyqtSlot()
    def _on_stop_clicked(self) -> None:
        """Handle stop button click."""
        self._clear_overlay_clone_pending()
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._app_selector.set_enabled(True)
        self._language_panel.set_enabled(True)
        self._capture_mode_combo.setEnabled(True)
        self._capture_channel_combo.setEnabled(True)
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

        self._hide_live_overlay()
        self._restore_main_window()

    def _handle_translation_error(self, error_msg: str) -> None:
        """Handle translation error from async worker."""
        try:
            logger.error("Translation failed", error=error_msg)
            self.show_error(f"Translation failed: {error_msg}")
        except Exception as e:
            logger.exception("Error showing translation failure", error=str(e))

        # Reset UI state (guard each in case widgets were destroyed)
        try:
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            if hasattr(self, "_app_selector") and self._app_selector:
                self._app_selector.set_enabled(True)
                self._app_selector._update_info_label()
            if hasattr(self, "_language_panel") and self._language_panel:
                self._language_panel.set_enabled(True)
                self._language_panel._on_language_changed()
            if hasattr(self, "_capture_mode_combo") and self._capture_mode_combo:
                self._capture_mode_combo.setEnabled(True)
            if hasattr(self, "_capture_channel_combo") and self._capture_channel_combo:
                self._capture_channel_combo.setEnabled(True)
            self._is_running = False
            if hasattr(self, "_clone_progress") and self._clone_progress:
                self._clone_progress.setValue(0)
                self._clone_progress.setFormat("Ready")
        except Exception as e:
            logger.exception("Error resetting UI after translation failure", error=str(e))
        self._hide_live_overlay()
        self._restore_main_window()

    @pyqtSlot(object)
    def _on_app_initialized(self, event: Event) -> None:
        """Handle app initialized event."""
        self._update_capture_mode_combo()
        self._populate_capture_channels()
        self._update_play_as_mic_ui()
        self._refresh_sessions()
        self._status_bar.set_app_state(AppState.READY)

        # Show API banner only when neither backend auth nor a direct API key is present
        has_key = (
            bool(self._settings.get_elevenlabs_api_key())
            or self._settings.is_token_valid()
        )
        self._api_banner.setVisible(not has_key)
        self._status_bar.set_api_status(has_key)

    def _update_capture_mode_combo(self) -> None:
        """Update capture mode combo. Selected app uses process loopback or VB-Cable."""
        if not self._orchestrator:
            return
        plb = self._orchestrator.is_process_loopback_supported
        self._capture_mode_combo.blockSignals(True)
        self._capture_mode_combo.setItemText(0, "Selected app only")
        self._capture_mode_combo.setItemData(
            0, "process_loopback" if plb else "vbcable"
        )
        self._capture_mode_combo.setToolTip(
            "Selected app only: Per-app capture via process loopback (Win 10 21H2+). "
            "If unsupported, uses all system audio.\n"
            "All system audio: Captures everything — built-in, no setup required."
        )
        self._capture_mode_combo.setCurrentIndex(1)
        self._capture_mode_combo.blockSignals(False)

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
        if self._overlay_clone_pending and self._dubbed_window is not None:
            self._dubbed_window.set_status(f"Recording speech… {pct}%")

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
        self._refresh_profile_list()
        if event.data.get("is_temporary"):
            return
        self._clear_overlay_clone_pending()
        if self._dubbed_window is not None:
            self._dubbed_window.set_cloning(False)
            self._sync_overlay_state()
            if voice_name:
                self._dubbed_window.set_status(f"Voice cloned: {voice_name}")

    @pyqtSlot(object)
    def _on_clone_failed(self, event: Event) -> None:
        """Handle voice clone failure."""
        error_msg = event.data.get("error", "Unknown error")
        self._clone_progress.setValue(0)
        self._clone_progress.setFormat(f"Clone failed: {error_msg}")
        # Re-enable buttons
        self._capture_voice_btn.setText("Capture Voice")
        self._capture_voice_btn.setEnabled(True)
        self._import_voice_btn.setText("Import Voice")
        self._import_voice_btn.setEnabled(True)
        self._clear_overlay_clone_pending()
        if self._dubbed_window is not None:
            self._dubbed_window.set_cloning(False)
            short = error_msg.split("\n")[0].strip()
            if len(short) > 80:
                short = short[:77] + "..."
            self._dubbed_window.set_status(
                f"Clone failed: {short}" if short else "Clone failed"
            )

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
        # Keep profile assign combo in sync with clone library
        self._populate_profile_voice_combo()
        if self._dubbed_window is not None:
            self._dubbed_window.set_voices(
                self._overlay_voice_items(),
                self._settings.voice_clone.default_voice_id,
            )

    def _refresh_profile_list(self) -> None:
        """Reload voice profiles and default-profile combo."""
        if not hasattr(self, "_profile_list"):
            return

        self._updating_profile_ui = True
        try:
            profiles = self._orchestrator.get_voice_profiles()
            default_id = self._orchestrator.get_default_profile_id()
            active_id = self._orchestrator.get_active_profile_id()
            voices_by_id = {v.voice_id: v for v in self._orchestrator.get_saved_voices()}

            prev_id = None
            cur = self._profile_list.currentItem()
            if cur:
                prev_id = cur.data(Qt.ItemDataRole.UserRole)

            self._profile_list.clear()
            self._default_profile_combo.clear()
            self._default_profile_combo.addItem("(none)", None)

            for p in profiles:
                assigned = voices_by_id.get(p.assigned_voice_id) if p.assigned_voice_id else None
                assigned_label = assigned.name if assigned else "Unassigned"
                markers = []
                if p.id == default_id:
                    markers.append("default")
                if p.id == active_id:
                    markers.append("active")
                suffix = f" [{', '.join(markers)}]" if markers else ""
                display = f"{p.name}  —  {assigned_label}{suffix}"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, p.id)
                if p.id == active_id:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._profile_list.addItem(item)
                if p.id == prev_id:
                    self._profile_list.setCurrentItem(item)

                self._default_profile_combo.addItem(p.name, p.id)

            # Restore default combo selection
            if default_id:
                idx = self._default_profile_combo.findData(default_id)
                if idx >= 0:
                    self._default_profile_combo.setCurrentIndex(idx)

            count = len(profiles)
            self._profile_count_label.setText(
                f"{count} profile{'s' if count != 1 else ''}"
            )
            self._populate_profile_voice_combo()
        finally:
            self._updating_profile_ui = False

    def _populate_profile_voice_combo(self) -> None:
        """Fill the assign-clone combo for the selected profile."""
        if not hasattr(self, "_profile_voice_combo"):
            return
        was_updating = getattr(self, "_updating_profile_ui", False)
        self._updating_profile_ui = True
        try:
            self._profile_voice_combo.clear()
            self._profile_voice_combo.addItem("(unassigned)", None)
            for v in self._orchestrator.get_saved_voices():
                self._profile_voice_combo.addItem(v.name, v.voice_id)

            item = self._profile_list.currentItem() if hasattr(self, "_profile_list") else None
            if not item:
                return
            profile_id = item.data(Qt.ItemDataRole.UserRole)
            profile = next(
                (p for p in self._orchestrator.get_voice_profiles() if p.id == profile_id),
                None,
            )
            if profile and profile.assigned_voice_id:
                idx = self._profile_voice_combo.findData(profile.assigned_voice_id)
                if idx >= 0:
                    self._profile_voice_combo.setCurrentIndex(idx)
        finally:
            self._updating_profile_ui = was_updating

    @pyqtSlot(object)
    def _on_voice_profile_changed(self, event: Event) -> None:
        """Update active profile label when TTS voice switches."""
        name = event.data.get("profile_name") or ""
        voice = event.data.get("name") or ""
        if name:
            self._active_profile_label.setText(f"Active: {name}" + (f" → {voice}" if voice else ""))
        QTimer.singleShot(0, self._refresh_profile_list)

    def _on_profile_selection_changed(
        self,
        _current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if getattr(self, "_updating_profile_ui", False):
            return
        self._populate_profile_voice_combo()

    def _on_default_profile_changed(self, _index: int) -> None:
        if getattr(self, "_updating_profile_ui", False):
            return
        profile_id = self._default_profile_combo.currentData()
        if self._orchestrator.set_default_profile(profile_id):
            self._settings.voice_clone.default_profile_id = profile_id
            ConfigManager().save(self._settings)
            self._refresh_profile_list()

    def _on_profile_voice_assigned(self, _index: int) -> None:
        if getattr(self, "_updating_profile_ui", False):
            return
        item = self._profile_list.currentItem()
        if not item:
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        voice_id = self._profile_voice_combo.currentData()
        if self._orchestrator.set_profile_voice(profile_id, voice_id):
            self._refresh_profile_list()

    def _on_rename_profile_clicked(self) -> None:
        item = self._profile_list.currentItem()
        if not item:
            QMessageBox.information(self, "No Profile Selected", "Select a profile first.")
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        current_name = ""
        for p in self._orchestrator.get_voice_profiles():
            if p.id == profile_id:
                current_name = p.name
                break
        new_name, ok = QInputDialog.getText(
            self, "Rename Profile", "Profile name:", text=current_name
        )
        if ok and new_name.strip():
            if self._orchestrator.rename_profile(profile_id, new_name.strip()):
                self._refresh_profile_list()

    def _on_delete_profile_clicked(self) -> None:
        item = self._profile_list.currentItem()
        if not item:
            QMessageBox.information(self, "No Profile Selected", "Select a profile first.")
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        name = item.text().split("  —  ")[0]
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{name}'?\nIts speaker embedding will be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._orchestrator.delete_profile(profile_id):
            self._refresh_profile_list()

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

    def _on_rename_voice_clicked(self) -> None:
        """Rename the currently selected voice in the library."""
        item = self._voice_list.currentItem()
        if not item:
            QMessageBox.information(
                self,
                "No Voice Selected",
                "Select a voice from the list first.",
            )
            return

        voice_id = item.data(Qt.ItemDataRole.UserRole)
        current_name = item.data(Qt.ItemDataRole.UserRole + 1) or "Voice"
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Voice",
            "New name for this voice:",
            text=current_name,
        )
        if not ok or not new_name.strip():
            return
        if self._orchestrator.rename_voice(voice_id, new_name.strip()):
            QTimer.singleShot(0, self._refresh_voice_list)
        else:
            QMessageBox.warning(
                self,
                "Rename Failed",
                "Could not rename the voice. It may have been removed.",
            )

    # ── Account / Billing ────────────────────────────────────────────────

    def _on_upgrade_requested(self, url: str) -> None:
        """Handle upgrade request signal from usage meter."""
        import webbrowser  # noqa: PLC0415
        webbrowser.open(url)

    def _open_account_portal(self) -> None:
        """Open the Stripe Customer Portal (or upgrade page) in the browser."""
        import webbrowser  # noqa: PLC0415
        # Use checkout URL stored by the usage meter if available,
        # otherwise fall back to the website upgrade page.
        url = self._usage_meter._checkout_url or self._settings.get_upgrade_url()
        webbrowser.open(url)

    def _open_website(self) -> None:
        """Open the Live Translate website in the default browser."""
        import webbrowser  # noqa: PLC0415
        webbrowser.open(self._settings.get_website_url())

    def _open_account_on_web(self) -> None:
        """Open the account/dashboard page on the official website."""
        import webbrowser  # noqa: PLC0415
        webbrowser.open(self._settings.get_account_url())

    def _open_download(self) -> None:
        """Open the app download page on the official website."""
        import webbrowser  # noqa: PLC0415
        webbrowser.open(self._settings.get_download_url())

    def _on_sign_out(self) -> None:
        """Clear stored auth tokens and quit so the auth gate runs on next launch."""
        reply = QMessageBox.question(
            self,
            "Sign Out",
            "Are you sure you want to sign out?\n\n"
            "The application will close. You will need to log in again on next launch.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._usage_meter.stop_auto_refresh()
            self._settings.clear_auth_tokens()
            logger.info("User signed out; quitting")
            self._force_quit = True
            self.close()

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
        if self._dubbed_window is not None:
            self._dubbed_window.set_font_size(value)

    def _on_dubbed_text_opacity_changed(self, value: int) -> None:
        """Handle inline text opacity slider change."""
        self._dubbed_text_opacity_label.setText(f"{value}%")
        self._settings.ui.dubbed_text_opacity = value / 100.0
        # Apply to inline text display
        alpha = int(value * 2.55)
        self._translation_text.setStyleSheet(
            f"color: rgba(234, 234, 234, {alpha});"
        )
        if self._dubbed_window is not None:
            self._dubbed_window.set_text_opacity(value / 100.0)

    def _on_popout_clicked(self) -> None:
        """Show the overlay HUD without hiding the main window."""
        if self._dubbed_detached and self._dubbed_window is not None:
            self._dubbed_window.show()
            self._dubbed_window.raise_()
            self._dubbed_window.activateWindow()
            return
        self._show_live_overlay(hide_main=False)
        self._translation_group.hide()
        logger.info("Overlay HUD shown")

    def _on_reattach(self) -> None:
        """Hide the overlay HUD and restore the inline translation pane."""
        if self._dubbed_window is not None:
            self._dubbed_font_slider.blockSignals(True)
            self._dubbed_font_slider.setValue(self._dubbed_window.get_font_size())
            self._dubbed_font_slider.blockSignals(False)
            self._dubbed_font_label.setText(str(self._dubbed_window.get_font_size()))
            self._apply_dubbed_font(self._dubbed_window.get_font_size())

            self._dubbed_text_opacity_slider.blockSignals(True)
            self._dubbed_text_opacity_slider.setValue(
                int(self._dubbed_window.get_text_opacity() * 100)
            )
            self._dubbed_text_opacity_slider.blockSignals(False)
            self._dubbed_text_opacity_label.setText(
                f"{int(self._dubbed_window.get_text_opacity() * 100)}%"
            )

            self._dubbed_window.hide()
            self._dubbed_window.deleteLater()
            self._dubbed_window = None

        self._translation_group.show()
        self._dubbed_detached = False
        self._popout_btn.setText("Pop Out")
        self._popout_btn.setToolTip("Detach dubbed text into a floating overlay")
        logger.info("Overlay HUD closed")

    @pyqtSlot(object)
    def _on_transcription(self, event: Event) -> None:
        """Handle transcription update."""
        text = event.data.get("text", "") or ""
        self._transcription_text.append(text)
        scrollbar = self._transcription_text.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())
        if self._dubbed_window is not None:
            self._dubbed_window.append_source_text(text)

    @pyqtSlot(object)
    def _on_translation(self, event: Event) -> None:
        """Handle translation update."""
        text = event.data.get("text", "")
        if text:
            self._translation_text.append(text)
            scrollbar = self._translation_text.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())
            if self._dubbed_window is not None:
                self._dubbed_window.append_text(text)

    @pyqtSlot(object)
    def _on_tts_started(self, event: Event) -> None:
        """Highlight the overlay translation chunk currently being spoken."""
        text = event.data.get("text", "") or ""
        if self._dubbed_window is not None:
            self._dubbed_window.highlight_spoken_text(text)

    @pyqtSlot(object)
    def _on_state_changed(self, event: Event) -> None:
        """Handle app state change."""
        new_state = event.data.get("new_state")
        if isinstance(new_state, AppState):
            self._status_bar.set_app_state(new_state)
            if self._dubbed_window is not None and new_state == AppState.RUNNING:
                self._dubbed_window.set_status("Listening…")

    @pyqtSlot(object)
    def _on_translation_state_changed(self, event: Event) -> None:
        """Handle translation state change."""
        new_state = event.data.get("new_state")
        if isinstance(new_state, TranslationState):
            self._status_bar.set_translation_state(new_state)
            if self._dubbed_window is not None:
                labels = {
                    TranslationState.IDLE: "Ready",
                    TranslationState.WAITING_FOR_AUDIO: "Listening…",
                    TranslationState.CLONING_VOICE: "Cloning voice…",
                    TranslationState.TRANSLATING: "Translating",
                    TranslationState.PAUSED: "Paused",
                    TranslationState.ERROR: "Error",
                }
                self._dubbed_window.set_status(labels.get(new_state, "Listening…"))

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

    @pyqtSlot(object)
    def _on_process_loopback_failed(self, event: Event) -> None:
        """Process loopback failed; switch to in-app system loopback."""
        err = event.data.get("error", "")
        logger.warning("Process loopback failed, using system audio", error=err)

        if self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.fallback_to_system_loopback(),
            )

    def _on_auth_expired(self, event: Event) -> None:
        """Session expired; prompt user to sign in again."""
        if getattr(self, "_auth_expired_showing", False):
            return
        self._auth_expired_showing = True
        try:
            # Stop capture so STT stops hammering 401s while the user signs in
            if self._is_running and self._async_worker:
                self._async_worker.run_coroutine(self._orchestrator.stop_translation())
            self._restore_main_window()
            msg = event.data.get("message", "Session expired — please sign in again.")
            reply = QMessageBox.warning(
                self,
                "Session Expired",
                f"{msg}\n\nSign in again to continue using translation.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Ok,
            )
            if reply == QMessageBox.StandardButton.Ok:
                from live_dubbing.gui.widgets.login_dialog import LoginDialog
                login = LoginDialog(self._settings, parent=self)
                if login.exec() == QDialog.DialogCode.Accepted:
                    auth = getattr(login, "auth_response", {})
                    self._auth_response = auth
                    self._usage_meter.set_tier(auth.get("tier", "free"))
                    if auth.get("usage"):
                        self._usage_meter._on_usage_fetched(auth["usage"])
                    if self._async_worker:
                        self._async_worker.run_coroutine(
                            self._orchestrator.reinit_elevenlabs()
                        )
                    self._usage_meter.refresh()
                    self._status_bar.set_app_state(AppState.READY)
                    logger.info("Re-authenticated after session expiry")
                else:
                    logger.info("Re-login cancelled")
        finally:
            self._auth_expired_showing = False

    def show_error(self, message: str) -> None:
        """Display error message to user."""
        self._restore_main_window()
        QMessageBox.critical(self, "Error", message)

    def changeEvent(self, event: QEvent | None) -> None:
        """Minimize to tray when the setting is enabled."""
        super().changeEvent(event)
        if event is None or event.type() != QEvent.Type.WindowStateChange:
            return
        if (
            self.isMinimized()
            and self._settings.ui.minimize_to_tray
            and self._tray is not None
        ):
            QTimer.singleShot(0, self.hide)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Hide to overlay/tray while a session is running; otherwise quit."""
        tray_ok = self._tray is not None and self._settings.ui.minimize_to_tray
        if not self._force_quit and (self._is_running or tray_ok):
            if event is not None:
                event.ignore()
            self.hide()
            if self._is_running and self._dubbed_window is not None:
                self._dubbed_window.show()
                self._dubbed_window.raise_()
            elif self._tray is not None:
                self._tray.showMessage(
                    "Live Translate",
                    "Still running in the system tray.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )
            return

        self._clear_overlay_clone_pending()
        if self._is_running and self._async_worker:
            self._async_worker.run_coroutine(
                self._orchestrator.stop_translation()
            )
        # Stop mic translator and unsubscribe (embedded Mic Translate widget)
        self._mic_translate_widget.cleanup()
        self._usage_meter.stop_auto_refresh()
        self._settings.ui.window_x = self.x()
        self._settings.ui.window_y = self.y()
        self._settings.ui.window_width = self.width()
        self._settings.ui.window_height = self.height()

        # Save overlay geometry
        self._settings.ui.dubbed_window_detached = self._dubbed_detached
        if self._dubbed_window is not None:
            self._dubbed_window._save_geometry()
            self._dubbed_window.set_session_active(False)
            with contextlib.suppress(TypeError):
                self._dubbed_window.reattach_requested.disconnect()
            self._dubbed_window.close()
            self._dubbed_window = None

        if self._tray is not None:
            self._tray.hide()

        # Persist settings
        try:
            ConfigManager().save(self._settings)
        except Exception as e:
            logger.warning("Could not save settings on close", error=str(e))

        for unsub in self._unsubscribers:
            unsub()
        if event is not None:
            event.accept()
        QApplication.quit()
