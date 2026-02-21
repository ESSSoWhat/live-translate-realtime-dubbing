"""
Audio level meter widget.
"""


from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class AudioMeter(QWidget):
    """
    Visual audio level meter with peak hold.
    """

    def __init__(
        self,
        label: str = "Audio Level",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label_text = label
        self._level = 0.0
        self._peak = 0.0
        self._peak_decay_rate = 0.05
        self._is_speech = False

        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Label
        self._label = QLabel(self._label_text)
        self._label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._label)

        # Progress bar as meter
        self._meter = QProgressBar()
        self._meter.setMinimum(0)
        self._meter.setMaximum(100)
        self._meter.setValue(0)
        self._meter.setTextVisible(False)
        self._meter.setMaximumHeight(12)
        self._meter.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #333;
                border-radius: 3px;
                background-color: #1a1a1a;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 2px;
            }
            """
        )
        layout.addWidget(self._meter)

    def _setup_timer(self) -> None:
        """Set up decay timer for peak hold."""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._decay_peak)
        self._timer.start(50)  # 20 FPS

    def set_level(self, level: float, is_speech: bool = False) -> None:
        """
        Set the current audio level.

        Args:
            level: Audio level (0.0 to 1.0)
            is_speech: Whether speech is detected
        """
        self._level = max(0.0, min(1.0, level))
        self._is_speech = is_speech

        # Update peak
        if self._level > self._peak:
            self._peak = self._level

        # Update meter
        display_level = int(self._level * 100)
        self._meter.setValue(display_level)

        # Update color based on speech detection
        if is_speech:
            self._meter.setStyleSheet(
                """
                QProgressBar {
                    border: 1px solid #333;
                    border-radius: 3px;
                    background-color: #1a1a1a;
                }
                QProgressBar::chunk {
                    background-color: #2196F3;
                    border-radius: 2px;
                }
                """
            )
        else:
            self._meter.setStyleSheet(
                """
                QProgressBar {
                    border: 1px solid #333;
                    border-radius: 3px;
                    background-color: #1a1a1a;
                }
                QProgressBar::chunk {
                    background-color: #4CAF50;
                    border-radius: 2px;
                }
                """
            )

    def _decay_peak(self) -> None:
        """Decay the peak hold value."""
        if self._peak > self._level:
            self._peak -= self._peak_decay_rate
            if self._peak < self._level:
                self._peak = self._level

    def reset(self) -> None:
        """Reset the meter."""
        self._level = 0.0
        self._peak = 0.0
        self._meter.setValue(0)

    @property
    def level(self) -> float:
        """Get current level."""
        return self._level

    @property
    def peak(self) -> float:
        """Get peak level."""
        return self._peak

    @property
    def is_speech(self) -> bool:
        """Check if speech is detected."""
        return self._is_speech
