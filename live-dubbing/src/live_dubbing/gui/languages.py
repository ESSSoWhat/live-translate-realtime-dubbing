"""
Language configuration and display names.
"""


# Supported languages with their display names
SUPPORTED_LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese (Mandarin)"),
    ("id", "Indonesian"),
    ("th", "Thai"),
    ("ru", "Russian"),
    ("hi", "Hindi"),
    ("vi", "Vietnamese"),
    ("tl", "Filipino (Tagalog)"),
]

# Source languages include auto-detect
SOURCE_LANGUAGES = [("auto", "Auto-detect")] + SUPPORTED_LANGUAGES


def get_language_name(code: str) -> str:
    """
    Get display name for a language code.

    Args:
        code: Language code (e.g., "en", "ja")

    Returns:
        Display name (e.g., "English", "Japanese")
    """
    for lang_code, name in SOURCE_LANGUAGES:
        if lang_code == code:
            return name
    return code


def get_language_code(name: str) -> str:
    """
    Get language code from display name.

    Args:
        name: Display name (e.g., "English")

    Returns:
        Language code (e.g., "en")
    """
    for code, lang_name in SOURCE_LANGUAGES:
        if lang_name == name:
            return code
    return name


def get_source_languages() -> list[tuple[str, str]]:
    """Get list of source languages (with auto-detect)."""
    return SOURCE_LANGUAGES.copy()


def get_target_languages() -> list[tuple[str, str]]:
    """Get list of target languages."""
    return SUPPORTED_LANGUAGES.copy()


def is_language_supported(code: str) -> bool:
    """Check if a language code is supported."""
    return code in [c for c, _ in SOURCE_LANGUAGES]
