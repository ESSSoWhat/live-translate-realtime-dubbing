"""Locate the VB-Cable driver setup shipped with the desktop app."""

from __future__ import annotations

import sys
from pathlib import Path

PACK_DIR = "VBCABLE_Driver_Pack43"
SETUP_EXE = "VBCABLE_Setup_x64.exe"


def vbcable_setup_candidates(app_dir: Path, meipass: Path | None = None) -> list[Path]:
    """Places the driver setup may live, most specific first.

    The installer copies the pack next to LiveTranslate.exe. PyInstaller 6
    also stores the same datas under ``_internal``, which is not the path
    the setup wizard launches.
    """
    relative = Path(PACK_DIR) / SETUP_EXE
    candidates = [
        app_dir / relative,
        app_dir / "_internal" / relative,
    ]
    if meipass is not None:
        internal = meipass / relative
        if internal not in candidates:
            candidates.append(internal)
    return candidates


def find_vbcable_setup(
    app_dir: Path | None = None,
    meipass: Path | None = None,
) -> Path | None:
    """Return the bundled ``VBCABLE_Setup_x64.exe``, or None when it is absent."""
    if app_dir is None:
        if getattr(sys, "frozen", False):
            app_dir = Path(sys.executable).resolve().parent
        else:
            root = Path(__file__).resolve().parents[2]
            candidate = root / "third_party" / PACK_DIR / SETUP_EXE
            return candidate if candidate.is_file() else None
    if meipass is None:
        raw = getattr(sys, "_MEIPASS", None)
        meipass = Path(raw) if raw else None
    for path in vbcable_setup_candidates(app_dir, meipass):
        if path.is_file():
            return path
    return None
