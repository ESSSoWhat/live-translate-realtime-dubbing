"""
Usage meter widget — shows monthly dubbing quota and an upgrade button.

Refreshes automatically every 60 seconds by polling GET /api/v1/user/usage.
"""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

import httpx
import structlog
from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from live_dubbing.config.settings import AppSettings

logger = structlog.get_logger(__name__)


class _UsageFetcher(QThread):
    """Background thread to fetch usage without blocking the UI."""

    fetched = pyqtSignal(dict)
    failed = pyqtSignal()

    def __init__(self, base_url: str, access_token: str) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._token = access_token

    def run(self) -> None:
        """Fetch usage from backend and emit fetched or failed signal."""
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    f"{self._base_url}/api/v1/user/usage",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if response.status_code == 200:
                self.fetched.emit(response.json())
            else:
                logger.debug(
                    "Usage fetch failed",
                    status_code=response.status_code,
                    url=f"{self._base_url}/api/v1/user/usage",
                )
                self.failed.emit()
        except Exception as exc:
            logger.debug("Usage fetch failed", error=str(exc))
            self.failed.emit()


class UsageMeterWidget(QFrame):
    """
    Compact widget showing tier, quota progress, and upgrade button.

    Displays current tier label (Free / Starter / Pro), dubbing time
    progress bar (used / limit), and "Upgrade" button (opens Stripe
    checkout URL or browser). Refreshes every 60 seconds.
    Call set_tier() after login.
    """

    upgrade_requested = pyqtSignal(str)  # emits checkout URL

    def __init__(self, settings: "AppSettings", parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._usage: dict = {}
        self._checkout_url: str = ""
        self._fetcher: _UsageFetcher | None = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMaximumHeight(60)
        self._build_ui()

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)  # 60 seconds
        self._timer.timeout.connect(self.refresh)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Left: tier + progress
        left = QVBoxLayout()
        left.setSpacing(2)

        self._tier_label = QLabel("Free tier")
        self._tier_label.setStyleSheet("font-size: 11px; color: #aaa;")
        left.addWidget(self._tier_label)

        self._progress = QProgressBar()
        self._progress.setMaximumHeight(12)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setStyleSheet(
            "QProgressBar { border-radius: 4px; background: #2a2a2a; }"
            "QProgressBar::chunk { background: #4f8cff; border-radius: 4px; }"
        )
        left.addWidget(self._progress)

        self._usage_label = QLabel("Loading usage…")
        self._usage_label.setStyleSheet("font-size: 10px; color: #777;")
        left.addWidget(self._usage_label)

        layout.addLayout(left, stretch=1)

        # Right: upgrade button (hidden for paid tiers)
        self._upgrade_btn = QPushButton("⬆ Upgrade")
        self._upgrade_btn.setMaximumWidth(90)
        self._upgrade_btn.setStyleSheet(
            "QPushButton { background: #f0a020; color: black; border-radius: 5px; "
            "font-weight: bold; font-size: 11px; padding: 4px 8px; }"
            "QPushButton:hover { background: #e09010; }"
        )
        self._upgrade_btn.clicked.connect(self._on_upgrade_clicked)
        layout.addWidget(self._upgrade_btn)

    def start_auto_refresh(self) -> None:
        """Begin the 60-second auto-refresh cycle."""
        self._timer.start()
        self.refresh()

    def stop_auto_refresh(self) -> None:
        """Stop the 60-second auto-refresh timer."""
        self._timer.stop()

    def refresh(self) -> None:
        """Fetch latest usage in a background thread."""
        token = self._settings.get_access_token()
        if not token:
            self._usage_label.setText("Sign in to see usage")
            return
        if self._fetcher is not None and self._fetcher.isRunning():
            return  # already in flight

        self._usage_label.setText("Loading usage…")
        self._fetcher = _UsageFetcher(self._settings.get_backend_url(), token)
        self._fetcher.fetched.connect(self._on_usage_fetched)
        self._fetcher.failed.connect(self._on_fetcher_failed)
        self._fetcher.finished.connect(self._on_fetcher_finished)
        self._fetcher.start()

    def _on_fetcher_finished(self) -> None:
        """Clear reference and schedule fetcher for deletion (avoids using deleted object)."""
        if self._fetcher is not None:
            self._fetcher.deleteLater()
            self._fetcher = None

    def _on_fetcher_failed(self) -> None:
        """On fetch failure keep previous data; show message if we have none."""
        if not self._usage:
            self._usage_label.setText("Could not load usage")

    def _on_usage_fetched(self, data: dict) -> None:
        self._usage = data
        # Update tier from server so subscription level stays in sync
        tier = data.get("tier")
        if isinstance(tier, str) and tier.strip():
            self.set_tier(tier.strip().lower())
        self._update_display()

    def _update_display(self) -> None:
        used = int(self._usage.get("dubbing_seconds_used", 0) or 0)
        limit = int(self._usage.get("dubbing_seconds_limit", 1800) or 1800)

        used_min = used // 60
        limit_min = limit // 60
        pct = min(100, int(used / max(limit, 1) * 100))

        self._progress.setValue(pct)
        self._usage_label.setText(f"{used_min} / {limit_min} min dubbed this month")

        # Colour progress bar red when near limit
        if pct >= 90:
            self._progress.setStyleSheet(
                "QProgressBar { border-radius: 4px; background: #2a2a2a; }"
                "QProgressBar::chunk { background: #e05555; border-radius: 4px; }"
            )
        else:
            self._progress.setStyleSheet(
                "QProgressBar { border-radius: 4px; background: #2a2a2a; }"
                "QProgressBar::chunk { background: #4f8cff; border-radius: 4px; }"
            )

    def set_tier(self, tier: str) -> None:
        """Set the displayed tier label and show/hide upgrade button."""
        tier_names = {"free": "Free tier", "starter": "Starter", "pro": "Pro"}
        self._tier_label.setText(tier_names.get(tier, tier.title()))
        # Hide upgrade button for Pro users
        self._upgrade_btn.setVisible(tier != "pro")

    def set_checkout_url(self, url: str) -> None:
        """Set the upgrade/checkout URL used when quota is exceeded."""
        self._checkout_url = url

    def is_quota_exceeded(self) -> bool:
        """Return True when cached usage shows dubbing quota is exhausted."""
        if not self._usage:
            return False
        used = int(self._usage.get("dubbing_seconds_used", 0) or 0)
        limit = int(self._usage.get("dubbing_seconds_limit", 0) or 0)
        return limit > 0 and used >= limit

    def _on_upgrade_clicked(self) -> None:
        url = self._checkout_url or self._settings.get_upgrade_url()
        if not url or not isinstance(url, str) or not url.strip():
            return
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return
        self.upgrade_requested.emit(url)
        webbrowser.open(url)
