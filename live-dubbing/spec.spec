# PyInstaller spec for Live Translate (onedir, windowed, CPU-only torch)
# Build: pyinstaller spec.spec

import sys

block_cipher = None

a = Analysis(
    ["src/live_dubbing/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "live_dubbing",
        "live_dubbing.app",
        "live_dubbing.config.settings",
        "live_dubbing.core.events",
        "live_dubbing.core.orchestrator",
        "live_dubbing.core.state",
        "live_dubbing.gui.main_window",
        "structlog",
        "keyring",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["rthook_torch.py"],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveTranslate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LiveTranslate",
)
