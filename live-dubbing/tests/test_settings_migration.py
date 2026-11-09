"""Tests for settings migration and validation."""

import json
import tempfile
from pathlib import Path

import pytest

from live_dubbing.config.settings import AppSettings, ConfigManager


def test_localhost_url_migration():
    """Test that localhost URLs are migrated to production on load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        config_file = config_dir / "settings.json"

        # Create a config with localhost URL (simulating old dev settings)
        bad_config = {
            "audio": {},
            "voice_clone": {},
            "translation": {},
            "ui": {},
            "backend_base_url": "http://127.0.0.1:8000",
            "website_base_url": "https://www.livetranslate.net",
        }
        config_file.write_text(json.dumps(bad_config), encoding="utf-8")

        # Create config manager and load
        manager = ConfigManager(app_name="test")
        manager.config_dir = config_dir
        manager.config_file = config_file

        settings = manager.load()

        # Verify localhost was migrated to production
        assert "127.0.0.1" not in settings.backend_base_url
        assert "localhost" not in settings.backend_base_url.lower()
        assert settings.backend_base_url == "https://livetranslatedubtool-production.up.railway.app"

        # Verify migration was saved
        saved_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert "127.0.0.1" not in saved_data["backend_base_url"]


def test_localhost_url_prevented_on_save():
    """Test that localhost URLs cannot be saved to config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        config_file = config_dir / "settings.json"

        manager = ConfigManager(app_name="test")
        manager.config_dir = config_dir
        manager.config_file = config_file

        # Create settings with localhost URL
        settings = AppSettings()
        settings.backend_base_url = "http://localhost:8000"

        # Save should replace localhost with production URL
        manager.save(settings)

        # Verify saved config has production URL
        saved_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert "localhost" not in saved_data["backend_base_url"].lower()
        assert saved_data["backend_base_url"] == "https://livetranslatedubtool-production.up.railway.app"


def test_production_url_unchanged():
    """Test that production URLs are not modified."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        config_file = config_dir / "settings.json"

        manager = ConfigManager(app_name="test")
        manager.config_dir = config_dir
        manager.config_file = config_file

        # Create settings with production URL
        settings = AppSettings()
        prod_url = "https://livetranslatedubtool-production.up.railway.app"
        settings.backend_base_url = prod_url

        # Save and reload
        manager.save(settings)
        loaded = manager.load()

        # Verify URL is unchanged
        assert loaded.backend_base_url == prod_url


def test_get_backend_url_with_env_override():
    """Test that LIVE_TRANSLATE_BACKEND_URL env var overrides saved URL."""
    import os

    settings = AppSettings()
    settings.backend_base_url = "https://livetranslatedubtool-production.up.railway.app"

    # Without env var, should use saved URL
    assert settings.get_backend_url() == "https://livetranslatedubtool-production.up.railway.app"

    # With env var, should use env var (even if it's localhost for dev/testing)
    os.environ["LIVE_TRANSLATE_BACKEND_URL"] = "http://localhost:8000"
    try:
        assert settings.get_backend_url() == "http://localhost:8000"
    finally:
        del os.environ["LIVE_TRANSLATE_BACKEND_URL"]
