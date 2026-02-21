"""
VB-Cable Setup Wizard for guided audio routing configuration.
"""

import contextlib
import os
import subprocess
from collections.abc import Callable

import structlog
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

logger = structlog.get_logger(__name__)


class WizardPage(QWidget):
    """Base class for wizard pages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 20, 20, 20)
        self._layout.setSpacing(15)


class WelcomePage(WizardPage):
    """Welcome page explaining the setup wizard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel("Audio Routing Setup")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(title)

        explanation = QLabel(
            "This app needs to capture audio from a specific application.\n\n"
            "Windows doesn't natively support per-app audio capture, so we use "
            "VB-Audio Virtual Cable to route audio.\n\n"
            "This wizard will help you:\n"
            "• Check if VB-Cable is installed\n"
            "• Install VB-Cable if needed\n"
            "• Configure your app's audio output\n\n"
            "The setup only takes a few minutes."
        )
        explanation.setWordWrap(True)
        explanation.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._layout.addWidget(explanation)

        self._layout.addStretch()


class DetectionPage(WizardPage):
    """Page for detecting VB-Cable installation status."""

    detection_complete = pyqtSignal(bool)  # True if VB-Cable found

    def __init__(self, detect_func: Callable[[], bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._detect_func = detect_func

        title = QLabel("Checking for VB-Cable...")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        self._layout.addWidget(title)

        self._status_label = QLabel("Scanning audio devices...")
        self._layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
        self._layout.addWidget(self._progress)

        self._result_frame = QFrame()
        self._result_frame.setFrameShape(QFrame.Shape.StyledPanel)
        result_layout = QVBoxLayout(self._result_frame)

        self._result_icon = QLabel()
        self._result_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        result_layout.addWidget(self._result_icon)

        self._result_text = QLabel()
        self._result_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_text.setWordWrap(True)
        result_layout.addWidget(self._result_text)

        self._result_frame.hide()
        self._layout.addWidget(self._result_frame)

        self._layout.addStretch()

    def start_detection(self) -> None:
        """Start the detection process."""
        self._progress.show()
        self._result_frame.hide()
        self._status_label.setText("Scanning audio devices...")

        # Run detection after a short delay to show progress
        QTimer.singleShot(500, self._run_detection)

    def _run_detection(self) -> None:
        """Run the actual detection."""
        try:
            found = self._detect_func()
            self._show_result(found)
        except Exception as e:
            logger.exception("Detection failed", error=str(e))
            self._show_result(False)

    def _show_result(self, found: bool) -> None:
        """Show detection result."""
        self._progress.hide()
        self._result_frame.show()

        if found:
            self._result_icon.setText("✓")
            self._result_icon.setStyleSheet("color: #4CAF50; font-size: 48px;")
            self._result_text.setText(
                "VB-Audio Virtual Cable is installed!\n\n"
                "Click Next to configure audio routing."
            )
            self._status_label.setText("VB-Cable detected successfully.")
        else:
            self._result_icon.setText("✗")
            self._result_icon.setStyleSheet("color: #f44336; font-size: 48px;")
            self._result_text.setText(
                "VB-Audio Virtual Cable is not installed.\n\n"
                "Click Next to download and install it."
            )
            self._status_label.setText("VB-Cable not found.")

        self.detection_complete.emit(found)


class InstallPage(WizardPage):
    """Page for installing VB-Cable."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel("Install VB-Audio Virtual Cable")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        self._layout.addWidget(title)

        instructions = QLabel(
            "VB-Audio Virtual Cable is a free virtual audio device that allows "
            "routing audio between applications.\n\n"
            "Click the button below to open the download page:"
        )
        instructions.setWordWrap(True)
        self._layout.addWidget(instructions)

        download_btn = QPushButton("Open VB-Cable Download Page")
        download_btn.setMinimumHeight(40)
        download_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        download_btn.clicked.connect(self._open_download_page)
        self._layout.addWidget(download_btn)

        steps_group = QGroupBox("Installation Steps")
        steps_layout = QVBoxLayout(steps_group)

        steps = [
            "1. Download VBCABLE_Driver_Pack45.zip from the website",
            "2. Extract the ZIP file",
            "3. Right-click VBCABLE_Setup_x64.exe and select 'Run as administrator'",
            "4. Click 'Install Driver' in the installer",
            "5. Restart your computer when prompted",
            "6. Come back to this wizard after restart",
        ]

        for step in steps:
            step_label = QLabel(step)
            step_label.setStyleSheet("padding: 2px;")
            steps_layout.addWidget(step_label)

        self._layout.addWidget(steps_group)

        self._recheck_btn = QPushButton("Recheck After Installation")
        self._recheck_btn.clicked.connect(self._on_recheck)
        self._layout.addWidget(self._recheck_btn)

        self._layout.addStretch()

        self._recheck_callback: Callable | None = None

    def set_recheck_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for recheck button."""
        self._recheck_callback = callback

    def _open_download_page(self) -> None:
        """Open VB-Cable download page in browser."""
        import webbrowser
        webbrowser.open("https://vb-audio.com/Cable/")

    def _on_recheck(self) -> None:
        """Handle recheck button click."""
        if self._recheck_callback:
            self._recheck_callback()


class ConfigurationPage(WizardPage):
    """Page for configuring audio routing."""

    def __init__(self, app_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_name = app_name

        title = QLabel("Configure Audio Routing")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        self._layout.addWidget(title)

        self._intro = QLabel()
        self._layout.addWidget(self._intro)
        self._update_intro()

        # Method 1: Windows Settings
        method1_group = QGroupBox("Method 1: Windows Sound Settings (Recommended)")
        method1_layout = QVBoxLayout(method1_group)

        method1_steps = QLabel(
            "1. Right-click the speaker icon in the Windows taskbar\n"
            "2. Select 'Open Sound settings'\n"
            "3. Scroll down and click 'App volume and device preferences'\n"
            "4. Find your target application in the list\n"
            "5. Change its 'Output' dropdown to 'CABLE Input (VB-Audio Virtual Cable)'"
        )
        method1_steps.setWordWrap(True)
        method1_layout.addWidget(method1_steps)

        open_settings_btn = QPushButton("Open Sound Settings")
        open_settings_btn.clicked.connect(self._open_sound_settings)
        method1_layout.addWidget(open_settings_btn)

        self._layout.addWidget(method1_group)

        # Method 2: PowerShell (Advanced)
        method2_group = QGroupBox("Method 2: Quick Open Sound Mixer")
        method2_layout = QVBoxLayout(method2_group)

        method2_desc = QLabel(
            "Open the Windows Volume Mixer directly to configure app outputs:"
        )
        method2_desc.setWordWrap(True)
        method2_layout.addWidget(method2_desc)

        open_mixer_btn = QPushButton("Open Volume Mixer")
        open_mixer_btn.clicked.connect(self._open_volume_mixer)
        method2_layout.addWidget(open_mixer_btn)

        self._layout.addWidget(method2_group)

        self._layout.addStretch()

        # Confirmation checkbox
        self._confirm_check = QCheckBox("I have configured the audio routing")
        self._confirm_check.stateChanged.connect(self._on_confirm_changed)
        self._layout.addWidget(self._confirm_check)

    def set_app_name(self, name: str) -> None:
        """Set the target application name."""
        self._app_name = name
        self._update_intro()

    def _update_intro(self) -> None:
        """Update intro text with app name."""
        if self._app_name:
            self._intro.setText(
                f"Now configure Windows to route '{self._app_name}' audio "
                "through VB-Cable.\n\n"
                "Choose one of the methods below:"
            )
        else:
            self._intro.setText(
                "Configure Windows to route your target application's audio "
                "through VB-Cable.\n\n"
                "Choose one of the methods below:"
            )

    def _open_sound_settings(self) -> None:
        """Open Windows sound settings."""
        try:
            os.startfile("ms-settings:apps-volume")
        except Exception as e:
            logger.warning("Failed to open sound settings", error=str(e))
            # Fallback
            with contextlib.suppress(Exception):
                subprocess.Popen(["control", "mmsys.cpl", "sounds"])

    def _open_volume_mixer(self) -> None:
        """Open Windows volume mixer."""
        try:
            subprocess.Popen(["sndvol.exe"])
        except Exception as e:
            logger.warning("Failed to open volume mixer", error=str(e))

    def _on_confirm_changed(self, _state: int) -> None:
        """Handle confirmation checkbox change."""
        pass  # Parent dialog handles this

    def is_confirmed(self) -> bool:
        """Check if user confirmed configuration."""
        return self._confirm_check.isChecked()


class FallbackPage(WizardPage):
    """Page offering system loopback as fallback option."""

    use_fallback = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel("Alternative: System Audio Capture")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        self._layout.addWidget(title)

        explanation = QLabel(
            "If you don't want to install VB-Cable, you can use system audio "
            "capture instead.\n\n"
            "Limitations of system capture:\n"
            "• Captures ALL system audio, not just one app\n"
            "• Your voice and notifications will also be captured\n"
            "• Other apps' audio will be mixed in\n\n"
            "This is useful for quick testing but not recommended for "
            "production use."
        )
        explanation.setWordWrap(True)
        self._layout.addWidget(explanation)

        self._layout.addStretch()

        fallback_btn = QPushButton("Use System Audio (Not Recommended)")
        fallback_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        fallback_btn.clicked.connect(self.use_fallback.emit)
        self._layout.addWidget(fallback_btn)


class CompletePage(WizardPage):
    """Completion page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel("Setup Complete!")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(title)

        icon = QLabel("✓")
        icon.setStyleSheet("color: #4CAF50; font-size: 64px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(icon)

        self._message = QLabel(
            "Audio routing is configured!\n\n"
            "You can now start translation. The app will capture audio "
            "from VB-Cable.\n\n"
            "Remember: Keep your target app's output set to 'CABLE Input' "
            "while using translation."
        )
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._message)

        self._layout.addStretch()

    def set_fallback_mode(self, is_fallback: bool) -> None:
        """Update message for fallback mode."""
        if is_fallback:
            self._message.setText(
                "System audio capture is enabled!\n\n"
                "Note: ALL system audio will be captured, not just one app.\n\n"
                "For better isolation, consider installing VB-Cable later."
            )


class VBCableSetupWizard(QDialog):
    """
    Setup wizard for VB-Cable configuration.

    Guides users through:
    1. Detection of VB-Cable
    2. Installation if needed
    3. Audio routing configuration
    4. Optional fallback to system capture
    """

    # Signal emitted when setup is complete
    # Arguments: (use_vb_cable: bool, use_system_fallback: bool)
    setup_complete = pyqtSignal(bool, bool)

    def __init__(
        self,
        detect_func: Callable[[], bool],
        app_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._detect_func = detect_func
        self._app_name = app_name
        self._vb_cable_found = False
        self._use_fallback = False

        self.setWindowTitle("Audio Routing Setup")
        self.setMinimumSize(500, 450)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the wizard UI."""
        layout = QVBoxLayout(self)

        # Stacked widget for pages
        self._stack = QStackedWidget()

        # Create pages
        self._welcome_page = WelcomePage()
        self._stack.addWidget(self._welcome_page)

        self._detection_page = DetectionPage(self._detect_func)
        self._detection_page.detection_complete.connect(self._on_detection_complete)
        self._stack.addWidget(self._detection_page)

        self._install_page = InstallPage()
        self._install_page.set_recheck_callback(self._recheck_vb_cable)
        self._stack.addWidget(self._install_page)

        self._config_page = ConfigurationPage(self._app_name)
        self._stack.addWidget(self._config_page)

        self._fallback_page = FallbackPage()
        self._fallback_page.use_fallback.connect(self._on_use_fallback)
        self._stack.addWidget(self._fallback_page)

        self._complete_page = CompletePage()
        self._stack.addWidget(self._complete_page)

        layout.addWidget(self._stack)

        # Navigation buttons
        nav_layout = QHBoxLayout()

        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setEnabled(False)
        nav_layout.addWidget(self._back_btn)

        nav_layout.addStretch()

        self._skip_btn = QPushButton("Skip (Use System Audio)")
        self._skip_btn.clicked.connect(self._on_skip)
        self._skip_btn.hide()
        nav_layout.addWidget(self._skip_btn)

        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(self._go_next)
        self._next_btn.setDefault(True)
        nav_layout.addWidget(self._next_btn)

        layout.addLayout(nav_layout)

    def _go_next(self) -> None:
        """Go to next page."""
        current = self._stack.currentIndex()

        if current == 0:  # Welcome -> Detection
            self._stack.setCurrentIndex(1)
            self._detection_page.start_detection()
            self._back_btn.setEnabled(True)
            self._next_btn.setEnabled(False)

        elif current == 1:  # Detection -> Install or Config
            if self._vb_cable_found:
                self._stack.setCurrentIndex(3)  # Config
                self._skip_btn.hide()
            else:
                self._stack.setCurrentIndex(2)  # Install
                self._skip_btn.show()
            self._next_btn.setEnabled(True)

        elif current == 2:  # Install -> Detection (recheck)
            self._stack.setCurrentIndex(1)
            self._detection_page.start_detection()
            self._next_btn.setEnabled(False)
            self._skip_btn.hide()

        elif current == 3:  # Config -> Complete
            if self._config_page.is_confirmed():
                self._stack.setCurrentIndex(5)  # Complete
                self._next_btn.setText("Finish")
                self._back_btn.hide()
            else:
                QMessageBox.warning(
                    self,
                    "Configuration Required",
                    "Please confirm that you have configured audio routing."
                )

        elif current == 4:  # Fallback -> Complete
            self._stack.setCurrentIndex(5)
            self._complete_page.set_fallback_mode(True)
            self._next_btn.setText("Finish")
            self._back_btn.hide()

        elif current == 5:  # Complete -> Done
            self.setup_complete.emit(self._vb_cable_found, self._use_fallback)
            self.accept()

    def _go_back(self) -> None:
        """Go to previous page."""
        current = self._stack.currentIndex()

        if current == 1:  # Detection -> Welcome
            self._stack.setCurrentIndex(0)
            self._back_btn.setEnabled(False)
            self._next_btn.setEnabled(True)

        elif current == 2:  # Install -> Detection
            self._stack.setCurrentIndex(1)
            self._skip_btn.hide()

        elif current == 3:  # Config -> Detection
            self._stack.setCurrentIndex(1)

        elif current == 4:  # Fallback -> Install
            self._stack.setCurrentIndex(2)
            self._skip_btn.show()

    def _on_detection_complete(self, found: bool) -> None:
        """Handle detection completion."""
        self._vb_cable_found = found
        self._next_btn.setEnabled(True)

    def _recheck_vb_cable(self) -> None:
        """Recheck for VB-Cable after installation."""
        self._stack.setCurrentIndex(1)
        self._detection_page.start_detection()
        self._next_btn.setEnabled(False)

    def _on_skip(self) -> None:
        """Handle skip button - use system fallback."""
        result = QMessageBox.question(
            self,
            "Use System Audio?",
            "This will capture ALL system audio, not just your target app.\n\n"
            "Are you sure you want to continue without VB-Cable?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if result == QMessageBox.StandardButton.Yes:
            self._use_fallback = True
            self._stack.setCurrentIndex(4)  # Fallback page
            self._skip_btn.hide()

    def _on_use_fallback(self) -> None:
        """Handle fallback selection."""
        self._use_fallback = True
        self._stack.setCurrentIndex(5)  # Complete
        self._complete_page.set_fallback_mode(True)
        self._next_btn.setText("Finish")
        self._back_btn.hide()

    def set_app_name(self, name: str) -> None:
        """Set the target application name."""
        self._app_name = name
        self._config_page.set_app_name(name)
