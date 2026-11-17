"""The desktop app must use a real ffmpeg, not a package-manager shim."""

from __future__ import annotations

import os
from pathlib import Path

from live_dubbing.ffmpeg_bundle import (
    configure_pydub,
    find_ffmpeg,
    is_unusable_ffmpeg,
    tts_playback_failure_message,
)


def test_chocolatey_shim_is_rejected(tmp_path: Path) -> None:
    shim = tmp_path / "ffmpeg.exe"
    shim.write_bytes(b"MZ chocolatey shimgen stub")
    assert is_unusable_ffmpeg(shim)
    assert find_ffmpeg(app_dir=tmp_path, path_env="") is None


def test_bundled_ffmpeg_is_preferred_over_path(tmp_path: Path) -> None:
    internal = tmp_path / "_internal"
    internal.mkdir()
    bundled = internal / "ffmpeg.exe"
    bundled.write_bytes(b"real-ffmpeg")
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "ffmpeg.exe").write_bytes(b"path-ffmpeg")

    found = find_ffmpeg(app_dir=tmp_path, path_env=str(other))
    assert found == bundled


def test_large_binary_is_usable_without_reading_it_as_text(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"\x00" * 1_000_000)
    assert not is_unusable_ffmpeg(ffmpeg)
    assert find_ffmpeg(app_dir=tmp_path, path_env="") == ffmpeg


def test_missing_converter_message_does_not_ask_for_path() -> None:
    message = tts_playback_failure_message(ffmpeg_found=False)
    assert "PATH" not in message
    assert "bundled audio converter is missing" in message


def test_decode_error_is_included_once() -> None:
    message = tts_playback_failure_message(
        ffmpeg_found=True,
        error="Decoder failed\nsecond line that should stay out",
    )
    assert message.startswith("Translated audio could not be played. Decoder failed")
    assert "second line" not in message


def test_configure_pydub_points_at_the_bundled_binary(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"real-ffmpeg")
    from pydub import AudioSegment

    previous = AudioSegment.converter
    previous_path = os.environ.get("PATH", "")
    try:
        configure_pydub(ffmpeg)
        assert AudioSegment.converter == str(ffmpeg)
        assert os.environ["PATH"].startswith(str(tmp_path) + os.pathsep)
    finally:
        AudioSegment.converter = previous
        os.environ["PATH"] = previous_path
