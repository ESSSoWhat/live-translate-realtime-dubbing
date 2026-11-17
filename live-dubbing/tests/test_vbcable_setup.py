"""Where the bundled VB-Cable driver setup is found."""

from pathlib import Path

from live_dubbing.vbcable_setup import find_vbcable_setup


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")


def test_prefers_copy_beside_the_app(tmp_path: Path) -> None:
    beside = tmp_path / "VBCABLE_Driver_Pack43" / "VBCABLE_Setup_x64.exe"
    internal = tmp_path / "_internal" / "VBCABLE_Driver_Pack43" / "VBCABLE_Setup_x64.exe"
    _touch(beside)
    _touch(internal)

    assert find_vbcable_setup(tmp_path, meipass=tmp_path / "_internal") == beside


def test_falls_back_to_pyinstaller_internal_dir(tmp_path: Path) -> None:
    internal = tmp_path / "_internal" / "VBCABLE_Driver_Pack43" / "VBCABLE_Setup_x64.exe"
    _touch(internal)

    assert find_vbcable_setup(tmp_path) == internal


def test_missing_setup_returns_none(tmp_path: Path) -> None:
    assert find_vbcable_setup(tmp_path) is None


def test_source_tree_includes_the_driver_setup() -> None:
    found = find_vbcable_setup()
    assert found is not None
    assert found.name == "VBCABLE_Setup_x64.exe"
    assert found.is_file()
