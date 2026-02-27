@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  Start the Live Translate backend locally for development/testing.
REM
REM  Prerequisites:
REM    1. Copy .env.example → .env and fill in SUPABASE_URL,
REM       SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET, ELEVENLABS_API_KEY.
REM    2. pip install -r requirements.txt  (first time only)
REM
REM  Then run the desktop app with the backend override:
REM    set LIVE_TRANSLATE_BACKEND_URL=http://localhost:8000
REM    cd ..\live-dubbing && python -m live_dubbing
REM ─────────────────────────────────────────────────────────────────────────────

setlocal

REM Change to the directory that contains this script
cd /d "%~dp0"

REM Check .env exists
if not exist ".env" (
    echo ERROR: .env file not found.
    echo Copy .env.example to .env and fill in your Supabase credentials.
    pause
    exit /b 1
)

echo Starting Live Translate backend on http://localhost:8000 ...
echo Press Ctrl+C to stop.
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause
