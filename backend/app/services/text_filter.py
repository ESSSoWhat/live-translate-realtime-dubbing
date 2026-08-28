"""Strip ASR non-verbal markers before TTS (mirrors desktop text_filter)."""

from __future__ import annotations

import re

_BRACKET_RE = re.compile(
    r"\[(?:music|pause|laughter|laughing|applause|silence|inaudible|crosstalk"
    r"|background\s*noise|noise|sigh|sighing|cough|coughing|gasp|gasping"
    r"|crying|sobbing|sniffing|clearing\s*throat|breathing|exhale|inhale"
    r"|foreign|foreign\s*language|unintelligible|indiscernible"
    r"|blank_audio|no\s*speech|beep|bleep|censored"
    r"|phone\s*ringing|doorbell|alarm|static"
    r"|sound\s*effect|sfx|fx"
    r"|crowd|cheering|booing|clapping"
    r"|singing|humming|whistling"
    r"|playing|instrumental"
    r"|intro|outro|transition"
    r"|video\s*playing|audio\s*playing"
    r"|♪|♫|🎵|🎶)\]",
    re.IGNORECASE,
)

_PAREN_RE = re.compile(
    r"\((?:music|pause|laughter|laughing|applause|silence|inaudible|crosstalk"
    r"|background\s*noise|noise|sigh|sighing|cough|coughing|gasp|gasping"
    r"|crying|sobbing|sniffing|clearing\s*throat|breathing|exhale|inhale"
    r"|foreign|foreign\s*language|unintelligible|indiscernible"
    r"|blank_audio|no\s*speech|beep|bleep|censored"
    r"|phone\s*ringing|doorbell|alarm|static"
    r"|sound\s*effect|sfx|fx"
    r"|crowd|cheering|booing|clapping"
    r"|singing|humming|whistling"
    r"|playing|instrumental"
    r"|intro|outro|transition"
    r"|video\s*playing|audio\s*playing"
    r"|♪|♫|🎵|🎶)\)",
    re.IGNORECASE,
)

_MUSIC_SYMBOLS_RE = re.compile(r"[♪♫🎵🎶🎤🎸🎹🎺🎻]+")
_ANY_BRACKET_RE = re.compile(r"\[[^\]]{0,50}\]")
_EMPTY_BRACKET_RE = re.compile(r"\[\s*\]|\(\s*\)")
_ELLIPSIS_RE = re.compile(r"\.{3,}")
_ASTERISK_RE = re.compile(
    r"\*(?:music|pause|laughter|laughing|applause|silence|sigh|cough"
    r"|crying|singing|humming|whistling|gasps?|laughs?|sighs?|coughs?)\*",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s{2,}")
_SPEECH_RE = re.compile(r"\w", re.UNICODE)
# deep_translator scrapes translate.google.com; Google 500/1500 pages become "translations".
_GOOGLE_TRANSLATE_ERROR_RE = re.compile(
    r"error\s*500\s*\(\s*server\s*error\s*\)|"
    r"that's all we know|"
    r"that\u2019s all we know|"
    r"!!\s*1500",
    re.IGNORECASE,
)
# From the 500/1500 marker through Google's footer, or the rest of the string if truncated.
_GOOGLE_ERROR_BLOB_RE = re.compile(
    r"error\s*500\s*\(\s*server\s*error\s*\)[\s\S]*?"
    r"(?:that's all we know\.?|that\u2019s all we know\.?|$)",
    re.IGNORECASE,
)


def is_scraped_translate_error(text: str) -> bool:
    """True when Google Translate HTML error was scraped as the translation."""
    return bool(text and _GOOGLE_TRANSLATE_ERROR_RE.search(text))


def strip_scraped_translate_error(text: str) -> str:
    """Remove Google 500/1500 error-page text, including truncated snippets."""
    if not text:
        return ""
    cleaned = _GOOGLE_ERROR_BLOB_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def strip_non_verbal(text: str) -> str:
    """Remove bracketed/parenthetical ASR markers; return speech-only text for TTS."""
    if not text:
        return ""
    result = text
    result = _BRACKET_RE.sub("", result)
    result = _PAREN_RE.sub("", result)
    result = _ASTERISK_RE.sub("", result)
    result = _MUSIC_SYMBOLS_RE.sub("", result)
    result = _ANY_BRACKET_RE.sub("", result)
    result = _EMPTY_BRACKET_RE.sub("", result)
    result = _ELLIPSIS_RE.sub("", result)
    result = _WHITESPACE_RE.sub(" ", result).strip()
    # Skip TTS when only punctuation / symbols remain (e.g. "[]", "...").
    if result and _SPEECH_RE.search(result) is None:
        return ""
    return result
