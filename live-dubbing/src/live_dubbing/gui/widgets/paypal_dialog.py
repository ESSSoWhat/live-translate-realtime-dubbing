"""
PayPal checkout dialog — lets the user upgrade via PayPal from the desktop app.

Offers the subscription tiers (Starter, Pro) and the one-time Early Adopters plan.
On "Continue to PayPal" it asks the backend to create the order/subscription in a
background thread, then opens the returned PayPal approval URL in the system browser.
"""

# pylint: disable=E0611,C0415,W0718

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

import structlog
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from live_dubbing.services import paypal_checkout

if TYPE_CHECKING:
    from live_dubbing.config.settings import AppSettings

logger = structlog.get_logger(__name__)

# (label, tier, is_subscription)
_PLANS: list[tuple[str, str, bool]] = [
    ("Starter — 5 hrs/month (subscription)", "starter", True),
    ("Pro — 15 hrs/month (subscription)", "pro", True),
    ("Early Adopters — lifetime (one-time)", "early_adopters", False),
]


class _CheckoutWorker(QThread):
    """Creates the PayPal order/subscription off the UI thread."""

    succeeded = pyqtSignal(str)  # approval URL
    failed = pyqtSignal(str)

    def __init__(self, base_url: str, email: str, tier: str, is_subscription: bool) -> None:
        super().__init__()
        self._base_url = base_url
        self._email = email
        self._tier = tier
        self._is_subscription = is_subscription

    def run(self) -> None:
        """Call the backend and emit the approval URL or an error message."""
        try:
            if self._is_subscription:
                url = paypal_checkout.create_subscription(self._base_url, self._email, self._tier)
            else:
                url = paypal_checkout.create_order(self._base_url, self._email, self._tier)
            self.succeeded.emit(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PayPal checkout failed", error=str(exc))
            self.failed.emit("Could not start PayPal checkout. Please try again.")


class PayPalCheckoutDialog(QDialog):
    """Modal dialog for choosing a plan and starting PayPal checkout."""

    def __init__(self, settings: AppSettings, email: str = "", parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._worker: _CheckoutWorker | None = None
        self.setWindowTitle("Upgrade with PayPal")
        self.setMinimumWidth(420)
        self._build_ui(email)

    def _build_ui(self, email: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("Choose a plan")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        self._plan_combo = QComboBox()
        for label, _tier, _sub in _PLANS:
            self._plan_combo.addItem(label)
        layout.addWidget(self._plan_combo)

        layout.addWidget(QLabel("PayPal / account email:"))
        self._email_edit = QLineEdit(email)
        self._email_edit.setPlaceholderText("you@example.com")
        layout.addWidget(self._email_edit)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._continue_btn = QPushButton("Continue to PayPal")
        self._continue_btn.setStyleSheet(
            "QPushButton { background: #ffc439; color: #003087; font-weight: bold; "
            "border-radius: 5px; padding: 6px 14px; }"
            "QPushButton:hover { background: #f0b429; }"
            "QPushButton:disabled { background: #666; color: #ccc; }"
        )
        self._continue_btn.clicked.connect(self._on_continue)
        btn_row.addWidget(self._continue_btn)
        layout.addLayout(btn_row)

    def _on_continue(self) -> None:
        email = self._email_edit.text().strip()
        if "@" not in email or "." not in email:
            QMessageBox.warning(self, "Email Required", "Please enter a valid email address.")
            return
        _label, tier, is_sub = _PLANS[self._plan_combo.currentIndex()]
        self._continue_btn.setEnabled(False)
        self._status.setText("Contacting PayPal…")
        self._worker = _CheckoutWorker(self._settings.get_backend_url(), email, tier, is_sub)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_success(self, approval_url: str) -> None:
        webbrowser.open(approval_url)
        self._status.setText("Opened PayPal in your browser. Complete checkout there.")
        QMessageBox.information(
            self,
            "Continue in your browser",
            "We opened PayPal in your web browser.\n\n"
            "After you approve the payment, your plan updates automatically — "
            "reopen the app or wait a minute for your new quota.",
        )
        self.accept()

    def _on_failed(self, message: str) -> None:
        self._continue_btn.setEnabled(True)
        self._status.setText(message)
