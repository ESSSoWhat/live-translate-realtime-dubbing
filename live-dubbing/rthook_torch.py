# PyInstaller runtime hook: fix stdio + pre-import torch before PyQt6 loads
# 1. Redirects None stdout/stderr to a log file (windowed app on Windows).
# 2. Prevents the DLL conflict between torch's c10.dll and Qt6 DLLs.

import os
import sys

# ── Fix stdio for windowed (console=False) builds ──────────────────────────
# On Windows, PyInstaller windowed apps have sys.stdout/stderr == None.
# Any library that calls .write() on them will crash with:
#   AttributeError: 'NoneType' object has no attribute 'write'
#
# Redirect to a log file so we can capture output for debugging.
_log_file = None
if sys.stdout is None or sys.stderr is None:
    try:
        _log_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "Live Translate",
            "logs",
        )
        os.makedirs(_log_dir, exist_ok=True)
        _log_file = open(  # noqa: SIM115
            os.path.join(_log_dir, "rthook.log"), "a", encoding="utf-8",
        )
    except Exception:
        _log_file = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

if sys.stdout is None:
    sys.stdout = _log_file
if sys.stderr is None:
    sys.stderr = _log_file

# ── Ensure _internal and torch lib directories are on PATH ─────────────────
# _internal contains bundled binaries (ffmpeg, ffprobe) and torch DLLs.
_internal = os.path.join(os.path.dirname(sys.executable), "_internal")
if os.path.isdir(_internal):
    os.environ["PATH"] = _internal + os.pathsep + os.environ.get("PATH", "")

torch_lib = os.path.join(_internal, "torch", "lib")
if os.path.isdir(torch_lib):
    os.environ["PATH"] = torch_lib + os.pathsep + os.environ.get("PATH", "")
    # Also add via os.add_dll_directory (Python 3.8+ on Windows)
    try:
        os.add_dll_directory(torch_lib)
    except (OSError, AttributeError):
        pass

try:
    import torch  # noqa: F401
except Exception:
    pass
