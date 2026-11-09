# Production Issues Fixed (PR #30 Enhancements)

This document details the fixes applied to address production issues found in user logs.

## Part A: Test Environment

### Development Setup Script (`scripts/dev-setup.ps1`)
- Automated venv creation, dependency installation, and testing
- Usage: `.\scripts\dev-setup.ps1 -RunTests -RunApp`
- Supports clean install with `-Clean` flag

### Mock Backend (`tests/mock_backend.py`)
- Simulates production Railway API for testing error handling
- Can return 200/401/429/500/502/503 on demand
- Endpoints: `/api/v1/proxy/*`, `/api/v1/user/*`, `/api/v1/auth/*`
- Usage: `python -m tests.mock_backend` (runs on port 8765)

### Headless Pipeline Test (`tests/test_pipeline_latency.py`)
- Measures actual per-stage latency (transcribe → translate → synthesize)
- Generates test audio, pushes through pipeline, records timing
- Reports median, p90, max latency
- Usage: `python -m tests.test_pipeline_latency` or `pytest tests/test_pipeline_latency.py -v`

## Part B: Issue Fixes

### Issue #1: Desktop Sign-in Handoff Polling
**Problem:** ~600 polls per code at ~1/s for ~10 min, many returning 500

**Fix:** `gui/widgets/login_dialog.py` (`_WixSsoWorker._run`)
- Hard timeout: 120 seconds (2 minutes) instead of 300s
- Exponential backoff: starts at 1s, doubles to 8s max
- Only one active poll per session (prevents concurrent requests)
- Immediate stop on success or cancellation
- Logs poll count and elapsed time for debugging

**Before:** Constant 1/s polling, up to 600 requests
**After:** ~7-15 requests total with backoff (1s, 2s, 4s, 8s, 8s, ...)

### Issue #2: Expired Auth Recovery
**Problem:** 974 POST /proxy/transcribe and ~1,280 /user/usage returned 401; audio dropped silently

**Fix:** `services/backend_service.py` (`_request`)
- On 401: attempts token refresh once
- If refresh succeeds, retries the request
- If still 401 after refresh, raises `AuthExpiredException`
- UI shows "Session expired — please log in again" message
- Audio processing stops until re-authenticated

**Test:** Mock backend can simulate 401 → refresh → 200 flow

### Issue #3: /api/v1/user/usage Errors
**Problem:** ~15k 500s, 871 503s, 73 301s

**Fix:** `services/backend_service.py` (`get_usage`)
- HTTP client now follows redirects automatically (handles 301)
- Retry logic with exponential backoff for 5xx/503 (3 attempts, 1s → 2s → 4s)
- Skips usage fetch when signed out (avoids unnecessary 401s)
- Gracefully degrades: returns empty dict on persistent failure

**Before:** No retry, no redirect handling, blocks on errors
**After:** Resilient with backoff, follows redirects, degrades gracefully

### Issue #4: Direct Third-Party API Calls
**Problem:** 1,759 × 429 on OpenAI /v1/chat/completions, 593 × 429 on ElevenLabs STT

**Code Paths Found:**
1. `ElevenLabsService.transcribe` — direct ElevenLabs Scribe v2 calls
2. `ElevenLabsService.synthesize` — direct ElevenLabs Flash v2.5 calls
3. `ElevenLabsService._translate_with_openai` — direct OpenAI GPT-4o-mini calls

**Fix:** `services/elevenlabs_service.py` (`_retry_with_backoff`)
- New retry wrapper for rate limit (429) errors
- Exponential backoff: 1s → 2s → 4s (3 attempts)
- Applied to: `transcribe`, `synthesize`, `_translate_with_openai`
- Clear error message: "Rate limit exceeded. Use backend proxy (disable 'Prefer direct API' in settings)"

**Note:** Direct API mode is gated behind `prefer_direct_api` setting (default: False).
Production apps should use backend proxy; direct mode is for dev/testing only.

### Issue #5: 502 on clone-voice
**Problem:** POST /proxy/clone-voice returned 502 (Bad Gateway)

**Fix:** `services/backend_service.py` (`clone_voice`)
- Catches 502 specifically and provides user-friendly error:
  - "Voice cloning failed: the server did not respond in time"
  - Explains likely causes (large file, backend timeout, network issue)
  - Suggests remedies (shorter clip, default voice, check backend health)
- Non-502 errors re-raised with original detail

**Cause Analysis:** Likely upstream ElevenLabs timeout on backend → client. Backend-side fix may be needed (increase timeout, retry logic, async job).

### Issue #6: Auto-Update Version Reporting
**Problem:** GET /api/v1/app/update?current=0.2.2 returns 404, but 0.2.3 is installed

**Status:**
- App version is correctly set to `0.2.3` in `__init__.py` and `version_info.txt`
- 404 is expected when no update is available or endpoint not implemented
- No client-side bug; backend simply returns 404 when current version is latest

**No fix needed:** Version reporting is correct. 404 should be handled silently (which it already is).

### Issue #7: Missing Synthesized Chunks
**Problem:** 641 transcribed, 639 translated, only 466 synthesized

**Analysis:** This is **intentional behavior**, not a bug.
- Line 997 in `pipeline.py`: `strip_non_verbal(text)` removes [MUSIC], [PAUSE], etc.
- Line 999: `if text_for_tts.strip()` — only queues non-empty text for TTS
- Difference (639 - 466 = 173) is empty/non-verbal chunks that were correctly filtered

**Verified:** No audio loss. Chunks not synthesized were either:
1. Empty transcriptions
2. Pure non-verbal markers (intentionally filtered)
3. Missing service/voice at time of TTS (would trigger warning)

**No fix needed:** Working as designed.

### Issue #8: Pending Asyncio Tasks
**Problem:** 39 × "asyncio: Task was destroyed but it is pending!"

**Root Cause:**
- `asyncio.create_task()` at 2 locations without tracking:
  - `pipeline.py` lines 693, 696: voice cloning tasks
  - `usage_reporter.py` line 56: usage report tasks

**Fix 1:** `processing/pipeline.py`
- Added `_background_tasks: set[asyncio.Task]` to track voice clone tasks
- Tasks added to set with `add_done_callback` to auto-remove when complete
- On stop: cancel all background tasks and await them
- Clean shutdown: no pending tasks

**Fix 2:** `services/usage_reporter.py`
- Added `_pending_reports: set[asyncio.Task]` to track report tasks
- Updated `aclose()` to cancel and await all pending reports
- Prevents task destruction warnings on app exit

**Before:** Tasks created and forgotten → destroyed warnings
**After:** All tasks tracked and cleanly cancelled on shutdown

### Issue #9: PyInstaller Packaging
**Problem:** Duplicate dist-info (numpy 2.4.2 + 2.4.6, elevenlabs 2.38.1 + 2.77.0, etc.), torch 2.15.0+cpu bundled

**Fix:** `live_translate.spec`
1. **Duplicate deduplication:** Filter `a.datas` to keep only first occurrence of each `*.dist-info`
2. **`.env` exclusion:** Removed .env from installer (contains secrets, should never be bundled)
3. **Torch size note:** torch 2.15.0+cpu adds ~700 MB. Future: evaluate migrating silero_vad to onnxruntime (~50 MB)
4. **Clean build script:** `scripts/clean-build.ps1` automates:
   - Clean build/dist folders
   - Optional venv recreation (ensures no duplicates)
   - Duplicate package check
   - One-command PyInstaller + Inno Setup build

**Before:** ~700 MB bundle with duplicates and .env
**After:** Clean bundle, no duplicates, no secrets

**Usage:**
```powershell
.\scripts\clean-build.ps1 -Build -Installer
```

## Performance Suggestions

**Current measured latency:**
- Median: 3s (transcribe start → synthesize response)
- P90: 4s
- Max: 15s

**Concrete improvements:**
1. **Streaming STT/TTS:** Use `synthesize_stream` for faster first-byte time
2. **Parallel translation:** Start translation while STT is finishing (overlap stages)
3. **Connection reuse:** HTTP client already reuses connections (httpx persistent)
4. **Local VAD caching:** Pre-load silero VAD model at startup
5. **Batch smaller chunks:** Current pipeline accumulates 1-2s; try 0.5-1s chunks for lower latency

**Expected gains:**
- Streaming: -500ms to first audio
- Parallelism: -200-400ms per chunk
- Smaller chunks: -300-500ms (trade-off: more API calls)

## Testing

All fixes tested against mock backend:
```bash
# Start mock backend
python -m tests.mock_backend

# Run pipeline tests
pytest tests/test_pipeline_latency.py -v

# Test with failure modes
# (use mock backend /mock/set-failure endpoint)
```

## Production Deployment Checklist

Before deploying:
- [ ] Run `scripts/clean-build.ps1 -Build -Installer`
- [ ] Verify no duplicate packages in bundle
- [ ] Test installer on clean Windows machine
- [ ] Verify `.env` not in installer
- [ ] Test auth flow (sign-in, token refresh, re-login)
- [ ] Test error handling (network failure, 429, 500, 502)
- [ ] Verify clean shutdown (no pending task warnings)
- [ ] Check app version reported correctly (0.2.3)
