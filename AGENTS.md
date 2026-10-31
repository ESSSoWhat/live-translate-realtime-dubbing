# AGENTS.md

General build/run/test commands for every component live in [CLAUDE.md](CLAUDE.md)
and [README.md](README.md). Read those first; this file only adds cloud-agent
specifics.

## Cursor Cloud specific instructions

This is a multi-component monorepo. Only the **`backend/`** FastAPI service is
runnable and end-to-end testable in the Linux Cloud Agent VM. The other
components are intentionally out of scope here:

- `live-dubbing/` — Windows-only desktop app (PyQt6, WASAPI, `pyaudiowpatch`); cannot run on Linux.
- `mobile/` — Flutter app; no Flutter SDK / emulator / mic in the VM.
- `website/` and `native/whisper.cpp` — **uninitialized git submodules** (empty dirs). Run `git submodule update --init <path>` (needs network) before touching them; the marketing site is Wix-hosted, not this repo.
- `android/`, `wix-app/`, `mlops/`, `expo/` — optional; not part of core API testing.

### Backend (`backend/`) — the service to run/test

- Python deps are installed into a venv at `backend/.venv` (created by the update
  script). Use `backend/.venv/bin/python` / `backend/.venv/bin/pip` — there is no
  global venv activation.
- Python 3.12 is used here; CI/Dockerfile pin 3.11 but there is no hard version
  constraint and the suite passes on 3.12.
- **Run (dev):** from `backend/`, `.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`. Serves `/health`, `/`, `/docs`; real endpoints are under `/api/v1`.
- **Test:** from `backend/`, `.venv/bin/python -m pytest tests/ -v` (this is the authoritative check CI runs). `conftest.py` sets dummy `SUPABASE_*`/`ELEVENLABS_API_KEY`/`LT_SYNC_SECRET` env, so no real secrets are needed.
- **Lint:** `flake8` (config in `.flake8`) is installed by the update script. Backend CI does **not** run flake8 (only pytest); a few pre-existing E302/E303/E203 nits are present in existing code.
- **No env vars are required to boot.** Every `Settings` field defaults to empty
  (`app/config.py`); external integrations (Supabase, ElevenLabs, Stripe, Twilio,
  PayPal, Wix) simply return `503` at request time when unconfigured. Copy
  `backend/.env.example` to `backend/.env` and fill keys only to exercise those paths.
- The `/api/v1/proxy/translate` endpoint falls back to Google Translate
  (`deep-translator`) when `OPENAI_API_KEY` is absent, so translation works over
  the network without any API key (auth dependency still applies to the live route).
- `ffmpeg` (used by `pydub` for STT duration parsing) is preinstalled in the VM.
