# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Live Translate** is a real-time audio translation and voice-cloned dubbing platform. It captures audio, translates it using ElevenLabs APIs, and outputs dubbed audio using a cloned voice.

- **Status**: Alpha (v0.1.0)
- **Components**:
  - `live-dubbing/` - Windows desktop app (PyQt6 GUI, Python)
  - `backend/` - FastAPI server for API proxy, usage tracking, Wix sync and API-key auth
  - `mobile/` - Flutter mobile app (Android/iOS)
  - `website/` - Next.js (optional); production site and auth are on **Wix** (www.livetranslate.net)

## Build, Run, Test Commands

### Desktop App (live-dubbing/)
```bash
cd live-dubbing
python -m venv venv && venv\Scripts\activate
pip install -e ".[dev]"                         # Install with dev deps
python -m live_dubbing                          # Run app
pytest tests/ -v                                # Run tests
pytest tests/test_vad.py -v                     # Single test file
black src/ tests/ && ruff check src/ tests/     # Format & lint
mypy src/                                       # Type check
```

**Building Installer:**
```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu  # CPU-only for distribution
pyinstaller live_translate.spec --noconfirm
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

### Backend (backend/)
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env                            # Configure credentials
python -m uvicorn app.main:app --reload         # Run locally on :8000
pytest tests/ -v                                # Run tests
```

Connect desktop app to local backend:
```bash
set LIVE_TRANSLATE_BACKEND_URL=http://localhost:8000
```

### Mobile App (mobile/)
```bash
cd mobile
flutter pub get
flutter run                                     # Run on connected device
flutter build apk                               # Build Android APK
flutter test                                    # Run tests
```

### Website (website/)
```bash
cd website
npm install
npm run dev                                     # Dev server on :3000
npm run build                                   # Production build
npm run lint                                    # ESLint
```

## Architecture

### Desktop App Core Components

**Orchestrator** (`core/orchestrator.py`) - Central coordinator:
- State machine: INITIALIZING → READY → RUNNING → STOPPING
- Coordinates audio capture, processing pipeline, playback
- Handles voice cloning workflow

**Processing Pipeline** (`processing/pipeline.py`):
- Flow: Audio → VAD → STT → Translate → TTS → Output
- Silero VAD for voice detection, ElevenLabs Scribe v2 for STT, Flash v2.5 for TTS
- Multi-stage async queue processing

**EventBus** (`core/events.py`) - Thread-safe pub/sub:
- Qt signals marshal events to GUI thread
- All components communicate via event emission

**Audio Subsystem**:
- `capture.py`: WASAPI loopback via `pyaudiowpatch`
- `routing.py`: VB-Cable integration for per-app isolation
- `playback.py`: TTS output at 24kHz

### Backend Architecture

FastAPI server with routers in `app/routers/`:
- `auth.py` - API-key provisioning (Wix-only), legacy Supabase endpoints
- `proxy.py` - ElevenLabs API proxy with usage tracking (auth via API key)
- `billing.py` - **Wix sync** (POST `/billing/wix/sync`), optional Stripe/Qonversion
- `user.py` - User profile and usage (auth via API key)

Services in `app/services/`:
- `supabase_client.py` - Database operations
- `usage.py` - Usage metering and limits

### Mobile App Structure

Flutter app in `mobile/lib/`:
- `screens/` - UI screens (home, login, settings, paywall)
- `services/` - API client, auth, Qonversion (IAP)
- `features/mic_translate/` - Core translation feature
- `config/` - API configuration

## Code Conventions

- **Line length**: 100 characters (Python), default (Dart/TS)
- **Python**: snake_case, strict mypy types, async throughout
- **Qt slots/events**: camelCase (Qt convention override)
- **GUI thread safety**: Always use `EventBus.emit()` from async code
- **Dart**: Follow flutter_lints

### Async Worker Pattern (Desktop)
AsyncWorker (QThread) runs the orchestrator's async loop. GUI communicates via `run_coroutine()` with callbacks. Updates marshaled via `QTimer.singleShot()`.

## Windows-Specific Notes

- **VB-Audio Virtual Cable** required for per-app audio isolation
- Logs: `%LOCALAPPDATA%/Live Translate/logs/app.log`
- Settings: `%APPDATA%/LiveDubbing/settings.json`
- Auth tokens: Windows Credential Manager (keyring)
- Uses `WindowsSelectorEventLoopPolicy` for asyncio
- FFmpeg bundled at `_internal/ffmpeg.exe` in PyInstaller build

## Supported Languages

en, ja, ko, zh (Mandarin), id, th, ru, hi, vi, tl (Filipino/Tagalog)

## Environment Variables

**Desktop App:**
```
ELEVENLABS_API_KEY              # Direct API access (dev/offline)
LIVE_TRANSLATE_BACKEND_URL      # Backend URL override
```

**Backend (.env):**
```
SUPABASE_DB_URL                 # Postgres (usage/tiers); can be Supabase or any Postgres
WIX_SYNC_SECRET                 # Required for Wix Velo → POST /billing/wix/sync and POST /auth/api-key
ELEVENLABS_API_KEY              # For API proxy
# Optional: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY if using Supabase for DB; STRIPE_* if using Stripe
```

**Mobile:**
```
# Configured in lib/config/api_config.dart
```
