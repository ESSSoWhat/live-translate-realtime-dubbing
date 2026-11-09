"""
Mock backend server for testing — simulates the Live Translate Railway API.

Can return 200/401/429/500/502/503 responses on demand for testing error handling
without touching production. Runs as a standalone FastAPI server or pytest fixture.

Usage as standalone server:
    python tests/mock_backend.py

Usage in tests:
    from tests.mock_backend import mock_backend_fixture
    @pytest.fixture
    def backend(mock_backend_fixture): ...
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Response
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn


# ── Configuration ───────────────────────────────────────────────────────────

# Simulate various failure modes (set via env var or API call)
FAILURE_MODE = {
    "transcribe": None,  # None, "401", "429", "500", "502", "503"
    "translate": None,
    "synthesize": None,
    "clone_voice": None,
    "usage": None,
    "me": None,
    "handoff": None,
    "update": None,
}

# Simulated auth token (for testing)
VALID_TOKEN = "mock_access_token_12345"
VALID_REFRESH = "mock_refresh_token_67890"

# Simulated usage data
MOCK_USAGE = {
    "tier": "pro",
    "dubbing_seconds_used": 120,
    "dubbing_seconds_limit": 10800,
    "tts_chars_used": 15000,
    "tts_chars_limit": 500000,
    "stt_seconds_used": 90,
    "stt_seconds_limit": 5400,
    "voice_clones_used": 2,
    "voice_clones_limit": 10,
    "period_reset_date": "2026-12-31",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def check_auth(authorization: str | None) -> None:
    """Raise 401 if auth header is invalid."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    if token != VALID_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def simulate_failure(endpoint: str, status_code: int, detail: str = "") -> None:
    """Raise HTTPException if failure mode is active for this endpoint."""
    mode = FAILURE_MODE.get(endpoint)
    if mode == str(status_code):
        raise HTTPException(status_code=status_code, detail=detail or f"Simulated {status_code}")


# ── FastAPI App ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager."""
    print("🚀 Mock backend started")
    yield
    print("🛑 Mock backend stopped")


app = FastAPI(title="Live Translate Mock Backend", lifespan=lifespan)


# ── Configuration endpoints ─────────────────────────────────────────────────

@app.post("/mock/set-failure")
async def set_failure(endpoint: str, mode: str | None = None) -> dict:
    """Set failure mode for an endpoint (e.g. endpoint='transcribe', mode='500')."""
    FAILURE_MODE[endpoint] = mode
    return {"ok": True, "endpoint": endpoint, "mode": mode}


@app.get("/mock/status")
async def get_status() -> dict:
    """Get current failure modes."""
    return {"failure_modes": FAILURE_MODE, "valid_token": VALID_TOKEN}


# ── Auth endpoints ──────────────────────────────────────────────────────────

@app.post("/api/v1/auth/refresh")
async def refresh_token(body: dict) -> JSONResponse:
    """Refresh access token."""
    refresh = body.get("refresh_token")
    if refresh != VALID_REFRESH:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return JSONResponse({
        "access_token": VALID_TOKEN,
        "refresh_token": VALID_REFRESH,
    })


@app.get("/api/v1/auth/desktop-handoff/{code}")
async def desktop_handoff(code: str) -> JSONResponse:
    """Desktop sign-in handoff polling endpoint."""
    simulate_failure("handoff", 500, "Handoff backend error")
    
    # Simulate "not ready yet" most of the time
    # For testing, return ready=True after 2 seconds
    import random
    if random.random() < 0.1:  # 10% chance of being ready
        return JSONResponse({
            "ready": True,
            "api_key": VALID_TOKEN,
        })
    return JSONResponse({"ready": False})


# ── User endpoints ──────────────────────────────────────────────────────────

@app.get("/api/v1/user/usage")
async def get_usage(authorization: str | None = Header(None)) -> JSONResponse:
    """Get user usage statistics."""
    check_auth(authorization)
    simulate_failure("usage", 500, "Database error")
    simulate_failure("usage", 503, "Service unavailable")
    return JSONResponse(MOCK_USAGE)


@app.get("/api/v1/user/me")
async def get_me(authorization: str | None = Header(None)) -> JSONResponse:
    """Get user profile."""
    check_auth(authorization)
    simulate_failure("me", 500)
    return JSONResponse({
        "user_id": "mock_user_123",
        "email": "test@example.com",
        "tier": "pro",
        "usage": MOCK_USAGE,
    })


# ── Proxy endpoints (AI services) ───────────────────────────────────────────

@app.post("/api/v1/proxy/transcribe")
async def transcribe(
    authorization: str | None = Header(None),
    audio: UploadFile = File(...),
    language: str = Form("auto"),
    sample_rate: str = Form("16000"),
) -> JSONResponse:
    """Transcribe audio to text."""
    check_auth(authorization)
    simulate_failure("transcribe", 401, "Token expired")
    simulate_failure("transcribe", 429, "Rate limit exceeded")
    simulate_failure("transcribe", 500, "ElevenLabs API error")
    
    # Read and validate audio
    audio_bytes = await audio.read()
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file too small")
    
    # Return mock transcription
    return JSONResponse({
        "text": "This is a mock transcription of the audio.",
        "language_code": "en" if language == "auto" else language,
        "confidence": 0.95,
    })


@app.post("/api/v1/proxy/translate")
async def translate(
    authorization: str | None = Header(None),
    body: dict = None,
) -> JSONResponse:
    """Translate text."""
    check_auth(authorization)
    simulate_failure("translate", 401)
    simulate_failure("translate", 500)
    
    if not body:
        raise HTTPException(status_code=400, detail="Missing request body")
    
    text = body.get("text", "")
    target_lang = body.get("target_language", "en")
    
    return JSONResponse({
        "translated_text": f"[Translated to {target_lang}] {text}",
        "source_language": body.get("source_language", "auto"),
        "target_language": target_lang,
    })


@app.post("/api/v1/proxy/synthesize")
async def synthesize(
    authorization: str | None = Header(None),
    body: dict = None,
) -> Response:
    """Synthesize text to speech."""
    check_auth(authorization)
    simulate_failure("synthesize", 401)
    simulate_failure("synthesize", 429)
    simulate_failure("synthesize", 500)
    
    if not body:
        raise HTTPException(status_code=400, detail="Missing request body")
    
    # Return mock MP3 data (silent 1 second MP3)
    # Minimal valid MP3 file header + frame
    mock_mp3 = (
        b'\xff\xfb\x90\x00' +  # MP3 frame header
        b'\x00' * 100           # Padding
    )
    
    return Response(content=mock_mp3, media_type="audio/mpeg")


@app.post("/api/v1/proxy/clone-voice")
async def clone_voice(
    authorization: str | None = Header(None),
    audio: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
) -> JSONResponse:
    """Clone a voice."""
    check_auth(authorization)
    simulate_failure("clone_voice", 401)
    simulate_failure("clone_voice", 502, "Bad gateway - upstream timeout")
    simulate_failure("clone_voice", 500)
    
    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Audio too short for voice cloning")
    
    return JSONResponse({
        "voice_id": f"mock_voice_{int(time.time())}",
        "name": name,
    })


# ── App update endpoint ─────────────────────────────────────────────────────

@app.get("/api/v1/app/update")
async def check_update(current: str = "0.2.3") -> JSONResponse:
    """Check for app updates."""
    simulate_failure("update", 404, "No update available")
    
    # Mock: if current < 0.2.3, return update available
    if current < "0.2.3":
        return JSONResponse({
            "update_available": True,
            "latest_version": "0.2.3",
            "download_url": "https://github.com/ESSSoWhat/live-translate-realtime-dubbing/releases/download/v0.2.3/LiveTranslate-Setup.exe",
            "release_notes": "Bug fixes and performance improvements",
        })
    
    # Current version is latest
    return JSONResponse({
        "update_available": False,
        "latest_version": "0.2.3",
    })


# ── Pytest fixture ─────────────────────────────────────────────────────────

def run_server(port: int = 8765, host: str = "127.0.0.1") -> None:
    """Run the mock backend server (blocking)."""
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"\n🚀 Starting mock backend on http://127.0.0.1:{port}")
    print(f"Valid token: {VALID_TOKEN}\n")
    run_server(port=port)
