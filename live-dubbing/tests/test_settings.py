"""
Tests for configuration settings.
"""

import pytest
import tempfile
from pathlib import Path


class TestAppSettings:
    """Tests for AppSettings model."""

    def test_default_settings(self):
        """Default settings are valid."""
        from live_dubbing.config.settings import AppSettings

        settings = AppSettings()

        assert settings.audio.sample_rate == 16000
        assert settings.audio.chunk_size_ms == 100
        assert settings.voice_clone.dynamic_capture_duration_sec == 5.0
        assert settings.translation.default_target_language == "en"
        assert settings.ui.dark_mode is True

    def test_audio_config_validation(self):
        """Audio config validates ranges."""
        from live_dubbing.config.settings import AudioConfig

        # Valid config
        config = AudioConfig(sample_rate=44100, chunk_size_ms=200)
        assert config.sample_rate == 44100

        # Invalid sample rate should raise
        with pytest.raises(ValueError):
            AudioConfig(sample_rate=1000)  # Too low

    def test_voice_clone_config(self):
        """Voice clone config has correct defaults."""
        from live_dubbing.config.settings import VoiceCloneConfig

        config = VoiceCloneConfig()
        assert config.dynamic_capture_duration_sec == 5.0
        assert config.voice_stability == 0.5
        assert config.voice_similarity == 0.75


class TestConfigManager:
    """Tests for ConfigManager."""

    def test_load_default_settings(self):
        """Load returns defaults when no file exists."""
        from live_dubbing.config.settings import ConfigManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ConfigManager(app_name="TestApp")
            manager.config_dir = Path(tmpdir)
            manager.config_file = Path(tmpdir) / "settings.json"

            settings = manager.load()
            assert settings.audio.sample_rate == 16000

    def test_save_and_load_settings(self):
        """Settings can be saved and loaded."""
        from live_dubbing.config.settings import ConfigManager, AppSettings

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ConfigManager(app_name="TestApp")
            manager.config_dir = Path(tmpdir)
            manager.config_file = Path(tmpdir) / "settings.json"

            # Modify and save
            settings = AppSettings()
            settings.audio.sample_rate = 44100
            settings.translation.default_target_language = "ja"
            manager.save(settings)

            # Load and verify
            loaded = manager.load()
            assert loaded.audio.sample_rate == 44100
            assert loaded.translation.default_target_language == "ja"

    def test_reset_settings(self):
        """Settings can be reset to defaults."""
        from live_dubbing.config.settings import ConfigManager, AppSettings

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ConfigManager(app_name="TestApp")
            manager.config_dir = Path(tmpdir)
            manager.config_file = Path(tmpdir) / "settings.json"

            # Save modified settings
            settings = AppSettings()
            settings.audio.sample_rate = 44100
            manager.save(settings)

            # Reset
            reset_settings = manager.reset()
            assert reset_settings.audio.sample_rate == 16000


class TestSupportedLanguage:
    """Tests for SupportedLanguage enum."""

    def test_language_display_names(self):
        """Each language has a display name."""
        from live_dubbing.config.settings import SupportedLanguage

        assert SupportedLanguage.get_display_name("en") == "English"
        assert SupportedLanguage.get_display_name("ja") == "Japanese"
        assert SupportedLanguage.get_display_name("unknown") == "unknown"

    def test_get_all_languages(self):
        """Get all languages returns 11 languages."""
        from live_dubbing.config.settings import SupportedLanguage

        languages = SupportedLanguage.get_all_languages()
        assert len(languages) == 11

    def test_get_source_languages(self):
        """Source languages include auto-detect."""
        from live_dubbing.config.settings import SupportedLanguage

        languages = SupportedLanguage.get_source_languages()
        codes = [code for code, name in languages]
        assert "auto" in codes
        assert len(languages) == 12  # 11 languages + auto
