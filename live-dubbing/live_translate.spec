# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Live Translate.

Build with:
    pyinstaller live_translate.spec

Output:
    dist/LiveTranslate/  (one-dir bundle)
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

block_cipher = None

# FFmpeg path: set FFMPEG_DIR (e.g. to CI runner path) or defaults for local Windows build
_ffmpeg_dir = Path(os.environ.get("FFMPEG_DIR", r"S:\Coding project\ffmpeg\bin"))
_ffmpeg_exe = _ffmpeg_dir / "ffmpeg.exe"
_ffmpeg_probe = _ffmpeg_dir / "ffprobe.exe"

# Project paths
PROJECT_ROOT = Path(SPECPATH)
SRC_DIR = PROJECT_ROOT / "src"
VENV_SITE = PROJECT_ROOT / "venv" / "Lib" / "site-packages"

# Collect ALL torch and torchaudio submodules recursively so none are missed
torch_hiddenimports = collect_submodules("torch")
torchaudio_hiddenimports = collect_submodules("torchaudio")

# jaraco.* is required by pkg_resources (setuptools ≥ 67) at runtime.
# PyInstaller's pyi_rth_pkgres hook triggers this import, so we must bundle it.
jaraco_hiddenimports = collect_submodules("jaraco")

# numpy ≥ 1.25 / 2.x moved internals to numpy._core.
# collect_all captures hiddenimports + .pyd binaries + data files in one call.
# (collect_submodules alone misses the C-extension .pyd files like _multiarray_umath)
_numpy_datas, _numpy_binaries, numpy_hiddenimports = collect_all("numpy")

# ── Analysis ────────────────────────────────────────────────────────────────

a = Analysis(
    [str(SRC_DIR / "live_dubbing" / "__main__.py")],
    pathex=[str(SRC_DIR)],
    binaries=[
        # ffmpeg for pydub MP3 decoding (TTS audio conversion)
        (str(_ffmpeg_exe), "."),
        (str(_ffmpeg_probe), "."),
        # numpy C-extension .pyd binaries (e.g. _multiarray_umath, _umath_doc_generated)
        *_numpy_binaries,
    ],
    datas=[
        # App assets (logo)
        (
            str(SRC_DIR / "live_dubbing" / "gui" / "assets"),
            os.path.join("live_dubbing", "gui", "assets"),
        ),
        # Silero VAD model files
        (
            str(VENV_SITE / "silero_vad" / "data"),
            os.path.join("silero_vad", "data"),
        ),
        # _soundfile_data (libsndfile DLL)
        (
            str(VENV_SITE / "_soundfile_data"),
            "_soundfile_data",
        ),
        # _sounddevice_data (PortAudio DLLs)
        (
            str(VENV_SITE / "_sounddevice_data"),
            "_sounddevice_data",
        ),
        # numpy data files (via collect_all — includes .pyi stubs, testing data)
        *_numpy_datas,
        # .env file (Supabase config for OAuth login)
        (
            str(PROJECT_ROOT / ".env"),
            ".",
        ),
    ],
    hiddenimports=torch_hiddenimports + torchaudio_hiddenimports + jaraco_hiddenimports + numpy_hiddenimports + [
        # ── Silero VAD ──────────────────────────────────────────────────
        "silero_vad",
        "silero_vad.model",
        "silero_vad.utils_vad",
        # ── Audio ───────────────────────────────────────────────────────
        "sounddevice",
        "soundfile",
        "_sounddevice_data",
        "_soundfile_data",
        "pyaudiowpatch",
        "pycaw",
        "pycaw.pycaw",
        "pycaw.constants",
        "pycaw.callbacks",
        "scipy.signal",
        "scipy.fft",
        "scipy.fft._pocketfft",
        # ── PyQt6 ──────────────────────────────────────────────────────
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.sip",
        # ── ElevenLabs / APIs ───────────────────────────────────────────
        "elevenlabs",
        "elevenlabs.client",
        "httpx",
        "httpx._transports",
        "httpx._transports.default",
        "httpcore",
        "httpcore._async",
        "httpcore._sync",
        "h11",
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        "sniffio",
        "certifi",
        "openai",
        "deep_translator",
        "deep_translator.google",
        # ── Async / networking ──────────────────────────────────────────
        "aiohttp",
        "multidict",
        "yarl",
        "frozenlist",
        "aiosignal",
        "async_timeout",
        # ── Config / utils ──────────────────────────────────────────────
        "pydantic",
        "pydantic.deprecated",
        "pydantic_settings",
        "platformdirs",
        "keyring",
        "keyring.backends",
        "keyring.backends.Windows",
        "structlog",
        "structlog.dev",
        "structlog.processors",
        "psutil",
        # ── Standard library extras sometimes missed ────────────────────
        "asyncio",
        "queue",
        "ctypes",
        "ctypes.wintypes",
        "comtypes",
        "comtypes.client",
        "numpy",
        "numpy._core",
        "numpy._core.multiarray",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "rthook_torch.py")],
    excludes=[
        # Trim unnecessary large packages
        "matplotlib",
        "tkinter",
        "IPython",
        "notebook",
        "sphinx",
        "pytest",
        "mypy",
        "ruff",
        "black",
        "pip",
        # NOTE: Do NOT exclude setuptools — pkg_resources (and PyInstaller's
        # pyi_rth_pkgres runtime hook) depend on it and jaraco.* at runtime.
        "wheel",
        # NOTE: Do NOT exclude any torch.* submodules — torch needs its
        # internal stubs (cuda, distributed, testing, etc.) to initialise.
        # Excluding them causes "No module named ..." errors at runtime.
        "caffe2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── Filter out CUDA DLLs (this app only needs CPU for Silero VAD) ───────────
# CUDA DLLs require CUDA runtime which may not be installed on target machines.
# Removing them prevents "The specified procedure could not be found" errors.
cuda_dll_patterns = ('cuda', 'cudnn', 'cublas', 'cusparse', 'cufft', 'curand', 'nvrtc')
a.binaries = [
    (name, path, typ)
    for name, path, typ in a.binaries
    if not any(pattern in name.lower() for pattern in cuda_dll_patterns)
]

# ── PYZ (bytecode archive) ──────────────────────────────────────────────────

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ─────────────────────────────────────────────────────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # one-dir mode
    name="LiveTranslate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX can break torch DLLs
    console=False,  # GUI app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SRC_DIR / "live_dubbing" / "gui" / "assets" / "logo.ico"),
)

# ── COLLECT (one-dir bundle) ────────────────────────────────────────────────

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LiveTranslate",
)
