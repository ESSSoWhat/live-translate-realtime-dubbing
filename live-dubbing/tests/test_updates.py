"""Patch zip checks used before an update is applied."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from live_dubbing.updates import (
    UpdateOffer,
    offer_installed_patch,
    validate_patch_zip,
    version_is_newer,
)


def test_newer_version() -> None:
    assert version_is_newer("0.3.0", "0.2.2")
    assert version_is_newer("0.2.10", "0.2.2")
    assert not version_is_newer("0.2.2", "0.2.2")
    assert not version_is_newer("0.2.1", "0.2.2")


def test_patch_zip_rejects_parent_path(tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "nope")
    with pytest.raises(ValueError):
        validate_patch_zip(archive)


def test_patch_zip_accepts_install_layout(tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("LiveTranslate.exe", b"exe")
        bundle.writestr("_internal/app.py", b"code")
    validate_patch_zip(archive)


def test_offer_installed_patch_applies_without_prompt(monkeypatch, tmp_path: Path) -> None:
    offer = UpdateOffer(
        latest="0.3.0",
        notes="",
        patch_url="https://example.test/patch.zip",
        patch_sha256="a" * 64,
    )
    question = MagicMock()
    monkeypatch.setattr("live_dubbing.updates.fetch_update", lambda _url: offer)
    monkeypatch.setattr("live_dubbing.updates.install_dir", lambda: tmp_path)
    monkeypatch.setattr("live_dubbing.updates._download_and_launch", lambda _parent, _offer: True)
    monkeypatch.setattr("live_dubbing.updates.QMessageBox.question", question)
    assert offer_installed_patch(None, "https://example.test") is True
    question.assert_not_called()


def test_offer_installed_patch_skips_source_builds(monkeypatch) -> None:
    offer = UpdateOffer(
        latest="0.3.0",
        notes="",
        patch_url="https://example.test/patch.zip",
        patch_sha256="a" * 64,
    )
    monkeypatch.setattr("live_dubbing.updates.fetch_update", lambda _url: offer)
    monkeypatch.setattr("live_dubbing.updates.install_dir", lambda: None)
    assert offer_installed_patch(None, "https://example.test") is False
