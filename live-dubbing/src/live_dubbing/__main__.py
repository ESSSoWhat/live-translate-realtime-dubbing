"""
Entry point for the Live Dubbing application.
"""

import asyncio
import contextlib
import faulthandler
import io
import logging
import os
import sys
from typing import NoReturn


def _enable_faulthandler() -> None:
    """Enable faulthandler to capture segfaults and other low-level crashes.

    This helps debug crashes in native libraries like PortAudio (sounddevice).
    Output goes to stderr (redirected to app.log when windowed).
    """
    with contextlib.suppress(Exception):
        faulthandler.enable(all_threads=True)


def _fix_stdio_for_windowed_app() -> None:
    """Ensure sys.stdout/stderr are never None.

    When running as a PyInstaller windowed app (console=False) on Windows,
    sys.stdout and sys.stderr are None.  This causes any library that calls
    .write() on them (e.g. structlog's PrintLogger, print(), faulthandler)
    to raise ``AttributeError: 'NoneType' object has no attribute 'write'``.

    We redirect both to a log file so all write calls succeed and output
    is captured for debugging.  After the fallback is in place we attempt
    a UTF-8 upgrade for cases where a real console IS attached.
    """
    # --- Determine a log file path in LOCALAPPDATA ---
    _log_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "Live Translate",
        "logs",
    )
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, "app.log")

    # --- Fallback: replace None streams with log file (or devnull) ---
    _log_file = None
    if sys.stdout is None or sys.stderr is None:
        try:
            _log_file = open(_log_path, "a", encoding="utf-8")  # noqa: SIM115
        except Exception:
            _log_file = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

    if sys.stdout is None:
        sys.stdout = _log_file  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = _log_file  # type: ignore[assignment]

    # --- UTF-8 upgrade for real consoles ---
    if sys.stdout and getattr(sys.stdout, "encoding", "utf-8") != "utf-8":
        with contextlib.suppress(Exception):
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )

    if sys.stderr and getattr(sys.stderr, "encoding", "utf-8") != "utf-8":
        with contextlib.suppress(Exception):
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace"
            )


def _setup_file_logging() -> None:
    """Configure Python's logging module to write to a file with rotation.

    This captures output from all libraries that use the standard
    ``logging`` module, as well as structlog (when it falls back to
    stdlib).  The log file lives next to the stdio redirect log so
    all diagnostic information is in one place.

    Uses rotating file handler to prevent unbounded log growth.
    Defaults to INFO level (WARNING for noisy libraries) unless
    enable_debug_logging is set in settings.
    """
    from logging.handlers import RotatingFileHandler

    _log_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "Live Translate",
        "logs",
    )
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, "app.log")

    # Check debug setting (loads from config file if exists)
    _enable_debug = os.environ.get("LIVE_TRANSLATE_DEBUG", "").lower() in ("1", "true", "yes")

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if _enable_debug else logging.INFO)

    # Rotating file handler: 10 MB max per file, keep 3 backups
    try:
        fh = RotatingFileHandler(
            _log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG if _enable_debug else logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)

        # Quiet noisy libraries in production builds
        if not _enable_debug:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)
            logging.getLogger("urllib3").setLevel(logging.WARNING)
            logging.getLogger("elevenlabs").setLevel(logging.INFO)

    except Exception:
        pass  # Don't crash if logging setup fails


def _configure_structlog() -> None:
    """Configure structlog to use stdlib logging so output goes to the file.

    By default structlog uses PrintLogger which writes directly to
    sys.stdout.  In a windowed PyInstaller build sys.stdout may have
    been None at the time structlog's _output module was imported,
    causing the captured 'stdout' module variable to be None.
    Routing structlog through stdlib avoids this entirely and
    ensures all log output is captured in the log file.
    """
    import structlog

    # Check debug setting for consistent log level
    _enable_debug = os.environ.get("LIVE_TRANSLATE_DEBUG", "").lower() in ("1", "true", "yes")
    _log_level = logging.DEBUG if _enable_debug else logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _hide_child_console_windows() -> None:
    """Stop helper programs such as ffmpeg from flashing a console window.

    The windowed build has no console, so Windows opens a new one for every
    console program it starts.  pydub starts ffmpeg for each dubbed clip and
    accepts no ``creationflags``, so default them for every child process.
    GUI programs (sndvol, Control Panel) ignore the flag.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import subprocess

    original = subprocess.Popen.__init__
    if getattr(original, "hides_console", False):
        return

    def init(self: subprocess.Popen, *args: object, **kwargs: object) -> None:
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    init.hides_console = True  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = init  # type: ignore[method-assign]


def _configure_pydub_ffmpeg() -> None:
    """Point pydub at the bundled ffmpeg so MP3 decoding works in PyInstaller builds.

    pydub resolves ffmpeg at class-definition time via its own ``which()``
    function.  In a PyInstaller windowed app the subprocess PATH may not
    match ``os.environ["PATH"]``, so pydub falls back to a bare ``"ffmpeg"``
    command that ``CreateProcessW`` cannot find.  Setting the class attribute
    to an absolute path bypasses the search entirely.
    """
    import shutil

    # In a PyInstaller onedir bundle the layout is:
    #   dist/LiveTranslate/LiveTranslate.exe
    #   dist/LiveTranslate/_internal/ffmpeg.exe
    _internal = os.path.join(os.path.dirname(sys.executable), "_internal")
    ffmpeg_path = os.path.join(_internal, "ffmpeg.exe")
    ffprobe_path = os.path.join(_internal, "ffprobe.exe")

    if os.path.isfile(ffmpeg_path):
        from pydub import AudioSegment

        AudioSegment.converter = ffmpeg_path
        AudioSegment.ffprobe = ffprobe_path
        logging.getLogger(__name__).info(
            "pydub configured with bundled ffmpeg: %s", ffmpeg_path,
        )
        return

    # Development mode: ffmpeg should be on PATH already.
    if not shutil.which("ffmpeg"):
        logging.getLogger(__name__).warning(
            "ffmpeg not found on PATH; pydub MP3 decoding will fail. "
            "Install ffmpeg or ensure it is on your system PATH.",
        )


def _load_env_early() -> None:
    """Load .env file early so Supabase config is available for login dialog."""
    from live_dubbing.config.settings import _load_env_file
    _load_env_file()


def main() -> NoReturn:
    """Main entry point for the application."""
    # Ensure we're on Windows
    if sys.platform != "win32":
        print("Error: Live Dubbing is only supported on Windows.")
        sys.exit(1)

    # Fix stdio FIRST — everything else may log or print
    _fix_stdio_for_windowed_app()

    _hide_child_console_windows()

    # Load .env early for Supabase config
    _load_env_early()

    # Set up file logging so all output is captured
    _setup_file_logging()

    # Configure structlog to work with our fixed stdio
    _configure_structlog()

    # Enable faulthandler to capture low-level crashes
    _enable_faulthandler()

    # Configure pydub to find bundled ffmpeg (must be before any pydub import)
    _configure_pydub_ffmpeg()

    # Set up asyncio event loop policy for Windows.
    # ProactorEventLoop (default on Windows) avoids access violations in select();
    # use Selector only if you hit Proactor-specific issues (e.g. subprocess).
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # Pre-import torch before PyQt6 to avoid DLL loading conflict on Windows.
    # When Qt6 DLLs are loaded first, torch's c10.dll fails with an access
    # violation because both ship overlapping C-runtime dependencies.
    try:
        import torch  # noqa: F401
    except ImportError:
        pass
    except OSError as e:
        # CUDA DLL (c10_cuda.dll) can fail on machines without matching CUDA runtime.
        # The app only needs CPU (Silero VAD). Rebuild with CPU-only PyTorch:
        #   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
        # then run PyInstaller again.
        logging.getLogger(__name__).critical(
            "PyTorch failed to load (often due to CUDA DLL). "
            "This build may have been made with CUDA-enabled PyTorch. "
            "Use a build made with CPU-only PyTorch, or run from source with: "
            "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
        )
        raise SystemExit(1) from e

    # Import here to avoid circular imports and speed up --help
    from live_dubbing.app import Application

    app = Application()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
