"""Tests for logging configuration."""

import logging
import os
import tempfile
from pathlib import Path


def test_rotating_file_handler_configured():
    """Test that logging uses RotatingFileHandler."""
    # Import after setting up temp dir to avoid writing to real log location
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LOCALAPPDATA"] = tmpdir

        # Import the logging setup function
        from live_dubbing.__main__ import _setup_file_logging

        _setup_file_logging()

        # Check that root logger has a RotatingFileHandler
        root = logging.getLogger()
        from logging.handlers import RotatingFileHandler

        has_rotating = any(isinstance(h, RotatingFileHandler) for h in root.handlers)
        assert has_rotating, "Root logger should have a RotatingFileHandler"


def test_log_file_location():
    """Test that log file is created in correct location."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LOCALAPPDATA"] = tmpdir

        from live_dubbing.__main__ import _setup_file_logging

        _setup_file_logging()

        # Check log file exists
        expected_log_dir = Path(tmpdir) / "Live Translate" / "logs"
        expected_log_file = expected_log_dir / "app.log"

        # Log something to trigger file creation
        logging.info("test message")

        assert expected_log_dir.exists(), f"Log directory should exist: {expected_log_dir}"


def test_debug_mode_from_env():
    """Test that LIVE_TRANSLATE_DEBUG env var enables debug logging."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LOCALAPPDATA"] = tmpdir
        os.environ["LIVE_TRANSLATE_DEBUG"] = "1"

        try:
            # Clear existing handlers
            root = logging.getLogger()
            for handler in root.handlers[:]:
                root.removeHandler(handler)

            from live_dubbing.__main__ import _setup_file_logging

            _setup_file_logging()

            # Root logger should be set to DEBUG
            assert root.level == logging.DEBUG
        finally:
            del os.environ["LIVE_TRANSLATE_DEBUG"]


def test_production_mode_default():
    """Test that production mode defaults to INFO level."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LOCALAPPDATA"] = tmpdir
        # Ensure DEBUG env var is not set
        os.environ.pop("LIVE_TRANSLATE_DEBUG", None)

        # Clear existing handlers
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)

        from live_dubbing.__main__ import _setup_file_logging

        _setup_file_logging()

        # Root logger should be set to INFO in production
        assert root.level == logging.INFO


def test_noisy_libraries_quieted_in_production():
    """Test that httpx/httpcore are set to WARNING in production."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LOCALAPPDATA"] = tmpdir
        os.environ.pop("LIVE_TRANSLATE_DEBUG", None)

        # Clear existing handlers
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)

        from live_dubbing.__main__ import _setup_file_logging

        _setup_file_logging()

        # Check that noisy libraries are set to WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("urllib3").level == logging.WARNING
