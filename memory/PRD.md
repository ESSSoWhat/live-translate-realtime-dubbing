# Live Translate — PRD / Working Notes

## Problem statement
"Analyze app and identify what else needs to be done to finalize production." User is specifically stuck setting up the subscription service linking to the website (Wix).

## Product
Real-time audio translation + voice-cloned dubbing. Components:
- `backend/` — FastAPI (Supabase auth/DB, ElevenLabs proxy, Wix/Stripe/Qonversion billing sync, usage metering, per-user API keys).
- `mobile/` — Flutter (Android/iOS), API-key auth.
- `live-dubbing/` — Windows desktop (PyQt6).
- Wix site (www.livetranslate.net) — source of truth for members + Pricing Plans; `wix-app/velo-pages/*` Velo code.

## Architecture (billing/auth)
Wix Members + Pricing Plans → Velo calls `POST /api/v1/billing/wix/sync` and `POST /api/v1/auth/api-key` (auth: `WIX_SYNC_SECRET`). Backend upserts user by email, sets tier, issues API key. Apps use `Authorization: Bearer <api_key>`; tier/usage from `GET /user/usage`.

## Audit done (June 2026)
- Ran backend in sandbox: `/health` 200, tests 5 passed, guarded endpoints return 503 without config.
- Full report written to `/app/PRODUCTION_READINESS.md`.
- FIXED: `wix-app/velo-pages/app-auth-page.js` used undefined `wixLocation.query` → `wixLocationFrontend.query`. This crashed the SSO entry page (root cause candidate for "subscription linking stuck").

## Backlog to finalize production
- P0: ~~Confirm real Wix plan names and align `billing.py::_wix_plan_to_tier`~~ REFACTORED into editable `_WIX_PLAN_ID_TO_TIER` / `_WIX_PLAN_NAME_TO_TIER` / `_WIX_KEYWORD_TO_TIER` tables at top of billing.py. User must drop in their exact plan IDs/names (prefer plan-ID matching — stable).
- P0 (config): matching `WIX_SYNC_SECRET` (Wix Secrets ↔ backend), correct `BACKEND_URL` in `api-key.web.js` + `sync.web.ts`, publish `/api-key` (Members) + `/app-auth` (public), run `supabase_schema.sql`.
- P1: ~~Fix `auth.py::_default_usage`~~ DONE. ~~Add `early_adopters` to `sync.web.ts` MemberInfo~~ DONE. ~~Decide Stripe path~~ DONE — Stripe HIDDEN: /plans, /checkout, /portal all 503 when unconfigured; no client calls them; upgrades route to Wix /upgrade.
- P1 (deploy): mobile release signing + dart-defines; desktop CI `FFMPEG_DIR`; set `BACKEND_ENV=production` + explicit `BACKEND_CORS_ORIGINS`.
- P2: Migrate FastAPI `on_event` → lifespan; improve usage metering accuracy.

## Verified via testing_agent (iteration_1, 16/17 then 17/17 after fixes)
- Wix sync: 401 on missing/wrong secret; reaches DB (503 Supabase-not-configured in sandbox) with correct secret via `X-Wix-Sync-Secret` OR `Authorization: Bearer`; 422 on invalid email. This confirms the user's "silent linking failure" = secret mismatch (401).
- Stripe hidden: `/billing/plans` and `/checkout` return 503 (never 500).
- Startup log prints active plan->tier mapping.
- FIXED (from test report): `get_current_user` now returns 401 (was 422) when Authorization header missing; added exception chaining in billing.py; removed pre-existing unused imports (proxy.py, dependencies.py).
- Added `backend/server.py` shim so the Emergent supervisor (`uvicorn server:app`) can run this repo (prod still uses `app.main:app`). Sandbox `backend/.env` has WIX_SYNC_SECRET=test-secret-123, Supabase intentionally blank.

## Credentials
No secrets committed (no backend `.env`; `.env.example` present). All keys must be set in deploy env / Wix Secrets Manager.
NOTE: Wix Secrets Manager forbids secret names starting with `wix` → the Wix secret is named `LT_SYNC_SECRET` (Velo reads `getSecret('LT_SYNC_SECRET')`); its value must equal the backend env `WIX_SYNC_SECRET`. Fixed in api-key.web.js, sync.web.ts + setup docs after user hit "Some fields have invalid or missing information."
