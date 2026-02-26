# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Live Translate** is a Windows desktop application for real-time audio translation and voice-cloned dubbing. It captures audio from Windows applications, translates it using ElevenLabs APIs, and outputs dubbed audio using a cloned voice.

- **Status**: Alpha (v0.1.0)
- **Platform**: Windows 10/11 only
- **Stack**: Python desktop app (PyQt6 GUI) + FastAPI backend

## Build, Run, Test, Lint Commands

### Setup
```bash
cd live-dubbing
python -m venv venv
venv\Scripts\activate
pip install -e .              # Development install
pip install -e ".[dev]"       # With dev dependencies
```

### Run Application
```bash
live-dubbing                  # Via entry point
python -m live_dubbing        # Direct module
```

### Testing
```bash
pytest tests/                           # Run all tests
pytest tests/test_vad.py -v            # Run single test file
pytest tests/ -v --cov                 # With coverage
```

### Linting & Formatting
```bash
black src/ tests/                      # Format
ruff check src/ tests/                 # Lint
mypy src/                              # Type check
```

### Building Installer
```bash
pyinstaller spec.spec                  # Create PyInstaller bundle
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss  # Build installer
```

## Architecture

### Core Components

**Orchestrator** (`core/orchestrator.py`) - Central coordinator managing all subsystems:
- Manages state transitions (INITIALIZING → READY → RUNNING → etc.)
- Coordinates audio capture, processing pipeline, and playback
- Handles voice cloning workflow

**Processing Pipeline** (`processing/pipeline.py`) - Async real-time translation:
- Flow: Audio → VAD → STT → Translate → TTS → Output
- Uses Silero VAD for voice activity detection
- ElevenLabs Scribe v2 for STT, Flash v2.5 for TTS
- Multi-stage queue processing for parallel operations

**EventBus** (`core/events.py`) - Thread-safe communication:
- Uses Qt signals for GUI updates from async code
- All components communicate via event emission/subscription

**Main Window** (`gui/main_window.py`) - PyQt6 GUI:
- Subscribes to EventBus for reactive UI updates
- Manages async worker thread for background tasks

### Audio Subsystem

- **capture.py**: WASAPI loopback capture via `pyaudiowpatch`
- **routing.py**: VB-Cable integration for per-app audio isolation
- **playback.py**: TTS output playback at 24kHz

### Backend Service

Located in `backend/` - FastAPI server for monetized deployments:
- Proxies ElevenLabs API with auth and usage tracking
- Supabase for auth, Stripe for billing
- Desktop app authenticates via JWT tokens stored in Windows keyring

## Code Conventions

- **Line length**: 100 characters (configured in ruff, black, .flake8)
- **Type hints**: Strict mypy (disallow_untyped_defs)
- **Qt slots/events**: camelCase (Qt convention)
- **Python code**: snake_case
- **Async**: Used throughout for non-blocking I/O
- **GUI thread safety**: Always use `EventBus.emit()` for GUI updates from async code

### Async Worker Pattern
The application creates an AsyncWorker (QThread subclass) that runs the orchestrator's async loop in a separate thread. The GUI communicates via `run_coroutine()` with callbacks. GUI updates are marshaled via `QTimer.singleShot()`.

## Windows-Specific Notes

- **VB-Audio Virtual Cable** required for per-app audio isolation
- Logs written to `%LOCALAPPDATA%/Live Translate/logs/app.log`
- Settings stored in `%APPDATA%/LiveDubbing/settings.json`
- Auth tokens stored securely via Windows Credential Manager (keyring)
- Uses `WindowsSelectorEventLoopPolicy` for asyncio
- FFmpeg bundled at `_internal/ffmpeg.exe` in PyInstaller distribution

## Supported Languages

en, ja, ko, zh (Mandarin), id, th, ru, hi, vi, tl (Filipino/Tagalog)

## Environment Variables

```
ELEVENLABS_API_KEY          # Required for direct API access
OPENAI_API_KEY              # Optional, for OpenAI translation
SUPABASE_URL                # Backend only
SUPABASE_SERVICE_ROLE_KEY   # Backend only
```
