"""Startup replaces an older install with the latest GitHub setup file."""

from __future__ import annotations

from pathlib import Path

from live_dubbing.services.app_update import (
    choose_setup_asset,
    install_command,
    is_newer,
    parse_version,
    stage_update,
)


def _release(version: str = "0.2.12", size: int = 20) -> dict:
    return {
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": f"LiveTranslate-{version}-Setup.exe",
                "browser_download_url": f"https://example.test/{version}.exe",
                "size": size,
            },
            {
                "name": f"LiveTranslate-v{version}.zip",
                "browser_download_url": "https://example.test/a.zip",
            },
        ],
    }


def test_numeric_versions_order_the_patch_correctly() -> None:
    assert parse_version("v0.2.10") == (0, 2, 10)
    assert is_newer("0.2.10", "0.2.9")
    assert is_newer("0.2.9", "0.2.10") is False
    assert is_newer("0.2.11", "0.2.11") is False


def test_the_setup_exe_is_the_only_installer_asset() -> None:
    chosen = choose_setup_asset(_release())
    assert chosen == ("0.2.12", "https://example.test/0.2.12.exe", 20)
    assert choose_setup_asset({"tag_name": "v0.2.12", "assets": []}) is None


def test_a_newer_release_is_downloaded_and_installed() -> None:
    seen: list[str] = []

    def download(url: str, version: str, size: int) -> Path:
        seen.append(f"{version}:{size}:{url}")
        return Path("C:/Temp/LiveTranslate-0.2.12-Setup.exe")

    installed: list[Path] = []
    version = stage_update(
        _release(),
        "0.2.11",
        download=download,
        install=installed.append,
    )

    assert version == "0.2.12"
    assert seen == ["0.2.12:20:https://example.test/0.2.12.exe"]
    assert installed == [Path("C:/Temp/LiveTranslate-0.2.12-Setup.exe")]


def test_the_current_release_is_left_alone() -> None:
    def download(url: str, version: str, size: int) -> Path:
        raise AssertionError("download")

    assert (
        stage_update(
            _release("0.2.11"),
            "0.2.11",
            download=download,
            install=lambda _path: None,
        )
        is None
    )


def test_install_waits_for_this_process_then_reopens_the_app() -> None:
    command = install_command(
        Path(r"C:\Temp\Live Translate\Setup.exe"),
        Path(r"C:\Users\Me\AppData\Local\Live Translate\LiveTranslate.exe"),
        4242,
    )
    assert "Wait-Process -Id 4242" in command
    assert "/VERYSILENT" in command
    assert "LiveTranslate.exe" in command
    assert "''" not in command
