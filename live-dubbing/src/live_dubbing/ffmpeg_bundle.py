"""Find the ffmpeg build shipped beside the desktop app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Chocolatey puts a tiny shim on PATH. Copying that shim does not copy ffmpeg.
_SHIM_MARKERS = (b"chocolatey", b"shimgen")
_REAL_FFMPEG_BYTES = 1_000_000


def is_unusable_ffmpeg(path: Path) -> bool:
    """True when *path* is missing or is a package-manager shim."""
    try:
        size = path.stat().st_size
    except OSError:
        return True
    if size == 0:
        return True
    if size >= _REAL_FFMPEG_BYTES:
        return False
    try:
        blob = path.read_bytes().lower()
    except OSError:
        return True
    return any(marker in blob for marker in _SHIM_MARKERS)


def ffmpeg_search_dirs(
    app_dir: Path | None = None,
    path_env: str | None = None,
) -> list[Path]:
    """Directories that may hold ffmpeg, most specific first."""
    dirs: list[Path] = []
    if app_dir is not None:
        dirs.extend([app_dir / "_internal", app_dir])
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    raw = os.environ.get("PATH", "") if path_env is None else path_env
    for part in raw.split(os.pathsep):
        if part:
            dirs.append(Path(part))
    return dirs


def find_ffmpeg(
    app_dir: Path | None = None,
    path_env: str | None = None,
) -> Path | None:
    """Return a usable ffmpeg binary, or None."""
    if app_dir is None and getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    names = ("ffmpeg.exe", "ffmpeg")
    seen: set[Path] = set()
    for directory in ffmpeg_search_dirs(app_dir, path_env):
        for name in names:
            path = directory / name
            try:
                key = path.resolve()
            except OSError:
                key = path
            if key in seen:
                continue
            seen.add(key)
            if path.is_file() and not is_unusable_ffmpeg(path):
                return path
    return None


def configure_pydub(ffmpeg: Path) -> None:
    """Point pydub at *ffmpeg* and put its folder on PATH for ffprobe."""
    directory = str(ffmpeg.parent)
    current = os.environ.get("PATH", "")
    if not current.startswith(directory + os.pathsep) and current != directory:
        os.environ["PATH"] = directory + os.pathsep + current
    from pydub import AudioSegment

    AudioSegment.converter = str(ffmpeg)


def tts_playback_failure_message(*, ffmpeg_found: bool, error: str = "") -> str:
    """One dialog's worth of text when dubbed MP3 cannot be played."""
    if not ffmpeg_found:
        return (
            "Translated audio could not be played. "
            "The bundled audio converter is missing from this install."
        )
    detail = error.split("\n")[0].strip()
    if len(detail) > 140:
        detail = detail[:137] + "..."
    if detail:
        return f"Translated audio could not be played. {detail}"
    return "Translated audio could not be played. The audio converter failed."
