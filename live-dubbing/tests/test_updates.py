"""Patch zip checks used before an update is applied."""

from pathlib import Path

import pytest

from live_dubbing.updates import validate_patch_zip, version_is_newer


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
