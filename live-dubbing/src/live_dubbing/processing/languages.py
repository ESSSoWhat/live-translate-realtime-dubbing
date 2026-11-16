"""Language code helpers for translation and STT."""

from __future__ import annotations

# Map common STT / BCP-47 tags onto the ISO-639-1 codes Live Translate uses.
_LANGUAGE_ALIASES: dict[str, str] = {
    "english": "en",
    "eng": "en",
    "japanese": "ja",
    "jpn": "ja",
    "jp": "ja",
    "korean": "ko",
    "kor": "ko",
    "kr": "ko",
    "chinese": "zh",
    "mandarin": "zh",
    "cmn": "zh",
    "zho": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-tw": "zh",
    "zh-hant": "zh",
    "indonesian": "id",
    "ind": "id",
    "thai": "th",
    "tha": "th",
    "russian": "ru",
    "rus": "ru",
    "hindi": "hi",
    "hin": "hi",
    "vietnamese": "vi",
    "vie": "vi",
    "filipino": "tl",
    "tagalog": "tl",
    "tgl": "tl",
    "fil": "tl",
    "kazakh": "kk",
    "kaz": "kk",
    "spanish": "es",
    "spa": "es",
    "castilian": "es",
}


def normalize_language_code(value: str | None) -> str:
    """Collapse STT tags like ``en-US`` / ``eng`` to a base language code."""
    raw = (value or "").strip().lower().replace("_", "-")
    if not raw or raw == "auto":
        return "auto"
    if raw in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[raw]
    base = raw.split("-", 1)[0]
    return _LANGUAGE_ALIASES.get(base, base)


def same_language(left: str | None, right: str | None) -> bool:
    """True when both codes refer to the same base language."""
    a = normalize_language_code(left)
    b = normalize_language_code(right)
    if a == "auto" or b == "auto":
        return False
    return bool(a) and a == b
