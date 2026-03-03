"""
Main Application class for Live Dubbing.
"""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any

import structlog
from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from live_dubbing.config.settings import AppSettings, ConfigManager
from live_dubbing.core.events import EventBus
from live_dubbing.core.orchestrator import Orchestrator
from live_dubbing.gui.main_window import MainWindow

logger = structlog.get_logger(__name__)


class AsyncWorker(QThread):
    """Worker thread for running asyncio event loop."""

    error_occurred = pyqtSignal(str)

    def __init__(self, orchestrator: Orchestrator) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def run(self) -> None:
        """Run the asyncio event loop in this thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._running = True

        try:
            self._loop.run_until_complete(self._run_orchestrator())
        except Exception as e:
            logger.exception("Async worker error", error=str(e))
            self.error_occurred.emit(str(e))
        finally:
            self._loop.close()

    async def _run_orchestrator(self) -> None:
        """Keep the orchestrator running."""
        await self.orchestrator.initialize()
        while self._running:
            await asyncio.sleep(0.1)
        await self.orchestrator.shutdown()

    def run_coroutine(
        self,
        coro: Coroutine[Any, Any, Any],
        on_error: Callable[[str], None] | None = None,
        on_success: Callable[[Any], None] | None = None,
    ) -> Future[Any] | None:
        """
        Schedule a coroutine on the async worker's event loop (thread-safe).

        This allows the GUI thread to schedule async operations on the worker thread.

        Args:
            coro: The coroutine to run
            on_error: Optional callback function(error_msg: str) called if coroutine fails
            on_success: Optional callback function(result) called if coroutine succeeds

        Returns:
            concurrent.futures.Future or None if loop not running
        """
        if self._loop and self._running:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)

            # Add callbacks to handle success/failure.
            # done_callback runs on the asyncio thread, so we must marshal
            # GUI-touching callbacks (on_error / on_success) back to the
            # Qt main thread via QTimer.singleShot.
            if on_error or on_success:
                def done_callback(f: Future[Any]) -> None:
                    try:
                        result = f.result()  # Will raise if coroutine failed
                        if on_success:
                            QTimer.singleShot(0, lambda r=result: on_success(r))
                    except Exception as e:
                        if on_error:
                            QTimer.singleShot(0, lambda msg=str(e): on_error(msg))
                        else:
                            logger.exception("Unhandled error in coroutine", error=str(e))

                future.add_done_callback(done_callback)

            return future
        return None

    def stop(self) -> None:
        """Stop the async worker."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


class Application:
    """Main application class coordinating all components."""

    def __init__(self) -> None:
        self._qt_app: QApplication | None = None
        self._main_window: MainWindow | None = None
        self._orchestrator: Orchestrator | None = None
        self._async_worker: AsyncWorker | None = None
        self._event_bus: EventBus | None = None
        self._config_manager: ConfigManager | None = None
        self._settings: AppSettings | None = None

    def run(self) -> int:
        """Run the application and return exit code."""
        logger.info("Starting Live Translate application")

        # Install global exception hooks (main + threads) to catch uncaught exceptions
        self._install_exception_hook()
        self._install_thread_exception_hook()

        # Initialize configuration
        self._config_manager = ConfigManager()
        self._settings = self._config_manager.load()
        self._settings.set_openai_api_key_from_env()
        self._settings.set_elevenlabs_api_key_from_env()

        # Create Qt application
        self._qt_app = QApplication(sys.argv)
        self._qt_app.setApplicationName("Live Translate")
        self._qt_app.setApplicationVersion("0.1.0")

        # Set application icon
        import pathlib

        icon_path = pathlib.Path(__file__).parent / "gui" / "assets" / "logo.png"
        if icon_path.exists():
            self._qt_app.setWindowIcon(QIcon(str(icon_path)))

        # ── Auth gate ────────────────────────────────────────────────────
        # Show login dialog whenever there is no valid (non-expired) JWT stored.
        # The dialog stores the new tokens in settings (keyring) on success.
        from PyQt6.QtWidgets import QDialog  # noqa: PLC0415
        from live_dubbing.gui.widgets.login_dialog import LoginDialog  # noqa: PLC0415

        _auth_response: dict = {}
        if not self._settings.is_token_valid():
            _login = LoginDialog(self._settings, parent=None)
            if _login.exec() != QDialog.DialogCode.Accepted:
                logger.info("Login cancelled; exiting")
                return 0
            _auth_response = getattr(_login, "auth_response", {})
            self._settings.set_cached_user_info(
                _auth_response.get("user_id", ""),
                _auth_response.get("tier", "free"),
            )
        else:
            _auth_response = self._settings.get_cached_auth_response()

        def _log_user_id(val: str) -> str:
            if not val:
                return "?"
            import hashlib
            return hashlib.sha256(val.encode()).hexdigest()[:12]

        if _auth_response:
            logger.info(
                "User authenticated",
                user_id_hash=_log_user_id(_auth_response.get("user_id", "")),
                tier=_auth_response.get("tier", "?"),
            )

        # Create event bus for component communication
        self._event_bus = EventBus()

        # Create orchestrator
        self._orchestrator = Orchestrator(
            settings=self._settings,
            event_bus=self._event_bus,
        )

        # Create async worker thread (before main window so we can pass it)
        self._async_worker = AsyncWorker(self._orchestrator)

        # Create and show main window
        self._main_window = MainWindow(
            orchestrator=self._orchestrator,
            event_bus=self._event_bus,
            settings=self._settings,
            async_worker=self._async_worker,
            auth_response=_auth_response,
        )
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

        # Start async worker thread
        self._async_worker.error_occurred.connect(self._on_async_error)
        self._async_worker.start()

        # Connect cleanup
        self._qt_app.aboutToQuit.connect(self._cleanup)

        # Run Qt event loop
        return self._qt_app.exec()

    def _on_async_error(self, error: str) -> None:
        """Handle errors from async worker."""
        logger.error("Async error occurred", error=error)
        if self._main_window:
            self._main_window.show_error(f"Background error: {error}")

    def _install_exception_hook(self) -> None:
        """Install a global exception hook to catch uncaught exceptions."""
        import os
        original_hook = sys.excepthook

        def exception_hook(exc_type: type[BaseException], exc_value: BaseException | None, exc_tb: Any) -> None:
            """Custom exception hook that logs errors before exiting."""
            logger.exception(
                "Uncaught exception",
                exc_type=exc_type.__name__,
                exc_value=str(exc_value),
            )
            # Write traceback to crash log file
            import traceback
            try:
                _log_dir = os.path.join(
                    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "Live Translate",
                    "logs",
                )
                os.makedirs(_log_dir, exist_ok=True)
                _crash_path = os.path.join(_log_dir, "crash_excepthook.log")
                with open(_crash_path, "w", encoding="utf-8") as f:
                    f.write(f"Uncaught exception: {exc_type.__name__}: {exc_value}\n\n")
                    if exc_value is not None:
                        traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
            except Exception:
                pass
            # Print to stderr as well for visibility
            if exc_value is not None:
                traceback.print_exception(exc_type, exc_value, exc_tb)
            # Call original hook (excepthook accepts value=None per runtime)
            exc_val: BaseException = exc_value if exc_value is not None else BaseException()
            original_hook(exc_type, exc_val, exc_tb)

        sys.excepthook = exception_hook

    def _install_thread_exception_hook(self) -> None:
        """Install threading.excepthook so thread crashes get logged to file."""
        import os
        import threading
        import traceback

        def thread_exception_hook(args: threading.ExceptHookArgs) -> None:
            try:
                _log_dir = os.path.join(
                    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "Live Translate",
                    "logs",
                )
                os.makedirs(_log_dir, exist_ok=True)
                _crash_path = os.path.join(_log_dir, "crash_thread.log")
                with open(_crash_path, "w", encoding="utf-8") as f:
                    f.write(
                        f"Thread exception in {args.thread}:\n"
                        f"{args.exc_type.__name__}: {args.exc_value}\n\n"
                    )
                    if args.exc_value is not None:
                        traceback.print_exception(
                            args.exc_type, args.exc_value, args.exc_traceback, file=f
                        )
            except Exception:
                pass
            logger.exception(
                "Thread exception",
                thread=str(args.thread),
                exc_type=args.exc_type.__name__ if args.exc_type else "?",
                exc_value=str(args.exc_value),
            )

        threading.excepthook = thread_exception_hook

    def _cleanup(self) -> None:
        """Clean up resources before exit."""
        logger.info("Shutting down application")

        # Stop async worker
        if self._async_worker:
            self._async_worker.stop()
            self._async_worker.wait(5000)  # Wait up to 5 seconds

        # Save settings
        if self._config_manager and self._settings:
            self._config_manager.save(self._settings)

        logger.info("Application shutdown complete")
