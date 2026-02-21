"""
Text filtering utilities for cleaning transcription/translation output
before sending to TTS.

Removes non-verbal markers like [MUSIC], (laughter), ♪, etc. that
speech-to-text engines insert but should not be spoken aloud.
"""

import re

# ── Compiled patterns (built once at import time) ───────────────────────────

# Bracketed markers: [MUSIC], [PAUSE], [LAUGHTER], [APPLAUSE], [inaudible], ...
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

# Parenthesized markers: (music), (laughing), (applause), ...
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

# Music symbols and emoji (standalone or inline)
_MUSIC_SYMBOLS_RE = re.compile(r"[♪♫🎵🎶🎤🎸🎹🎺🎻]+")

# Catch-all for any remaining square-bracketed annotations
# e.g. [Speaker 1], [Background], [End], [00:01:23], etc.
# This is intentionally broad — anything in brackets is likely not speech.
_ANY_BRACKET_RE = re.compile(r"\[[^\]]{1,50}\]")

# Ellipsis artifacts (three or more dots — often ASR filler)
_ELLIPSIS_RE = re.compile(r"\.{3,}")

# Asterisk-wrapped markers: *music*, *laughs*, *applause*
_ASTERISK_RE = re.compile(
    r"\*(?:music|pause|laughter|laughing|applause|silence|sigh|cough"
    r"|crying|singing|humming|whistling|gasps?|laughs?|sighs?|coughs?)\*",
    re.IGNORECASE,
)

# Collapse multiple spaces/newlines into a single space
_WHITESPACE_RE = re.compile(r"\s{2,}")


def strip_non_verbal(text: str) -> str:
    """Remove non-verbal markers and ASR artifacts from text.

    Returns cleaned text suitable for TTS.  If the entire text consists
    of non-verbal content, returns an empty string so the caller can
    skip the TTS call entirely.

    Examples
    --------
    >>> strip_non_verbal("[MUSIC] Hello world [LAUGHTER]")
    'Hello world'
    >>> strip_non_verbal("[MUSIC]")
    ''
    >>> strip_non_verbal("He said (laughing) that was funny")
    'He said that was funny'
    >>> strip_non_verbal("♪ La la la ♪")
    'La la la'
    """
    if not text:
        return ""

    result = text

    # Remove specific known markers first (high confidence)
    result = _BRACKET_RE.sub("", result)
    result = _PAREN_RE.sub("", result)
    result = _ASTERISK_RE.sub("", result)
    result = _MUSIC_SYMBOLS_RE.sub("", result)

    # Remove any remaining bracketed annotations
    result = _ANY_BRACKET_RE.sub("", result)

    # Clean up ellipsis artifacts
    result = _ELLIPSIS_RE.sub("", result)

    # Normalize whitespace
    result = _WHITESPACE_RE.sub(" ", result).strip()

    return result
