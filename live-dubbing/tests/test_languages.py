"""
Tests for language configuration.
"""

import pytest


class TestLanguages:
    """Tests for language utilities."""

    def test_get_language_name(self):
        """Get display name for language code."""
        from live_dubbing.gui.languages import get_language_name

        assert get_language_name("en") == "English"
        assert get_language_name("ja") == "Japanese"
        assert get_language_name("zh") == "Chinese (Mandarin)"
        assert get_language_name("auto") == "Auto-detect"

    def test_get_language_code(self):
        """Get code from display name."""
        from live_dubbing.gui.languages import get_language_code

        assert get_language_code("English") == "en"
        assert get_language_code("Japanese") == "ja"
        assert get_language_code("Auto-detect") == "auto"

    def test_get_source_languages(self):
        """Get source languages includes auto-detect."""
        from live_dubbing.gui.languages import get_source_languages

        languages = get_source_languages()
        codes = [code for code, name in languages]

        assert "auto" in codes
        assert "en" in codes
        assert "ja" in codes

    def test_get_target_languages(self):
        """Get target languages excludes auto-detect."""
        from live_dubbing.gui.languages import get_target_languages

        languages = get_target_languages()
        codes = [code for code, name in languages]

        assert "auto" not in codes
        assert "en" in codes
        assert "ja" in codes

    def test_is_language_supported(self):
        """Check if language is supported."""
        from live_dubbing.gui.languages import is_language_supported

        assert is_language_supported("en")
        assert is_language_supported("ja")
        assert is_language_supported("auto")
        assert not is_language_supported("xx")

    def test_all_supported_languages(self):
        """Verify all 11 languages are supported."""
        from live_dubbing.gui.languages import SUPPORTED_LANGUAGES

        expected_codes = ["en", "es", "ja", "ko", "zh", "id", "th", "ru", "hi", "vi", "tl"]
        actual_codes = [code for code, name in SUPPORTED_LANGUAGES]

        assert len(SUPPORTED_LANGUAGES) == 11
        for code in expected_codes:
            assert code in actual_codes
