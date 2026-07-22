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
- P0: Confirm real Wix plan names and align `billing.py::_wix_plan_to_tier` (or send `tier` from Velo) so paid users get correct tier.
- P0 (config): matching `WIX_SYNC_SECRET` (Wix Secrets ↔ backend), correct `BACKEND_URL` in `api-key.web.js` + `sync.web.ts`, publish `/api-key` (Members) + `/app-auth` (public), run `supabase_schema.sql`.
- P1: Fix `auth.py::_default_usage` limits to match schema (starter 18000s, pro 54000s). Add `early_adopters` to `sync.web.ts` MemberInfo type. Decide Stripe path (fill price IDs or hide).
- P1 (deploy): mobile release signing + dart-defines; desktop CI `FFMPEG_DIR`; set `BACKEND_ENV=production` + explicit `BACKEND_CORS_ORIGINS`.
- P2: Migrate FastAPI `on_event` → lifespan; improve usage metering accuracy.

## Credentials
No secrets committed (no backend `.env`; `.env.example` present). All keys must be set in deploy env / Wix Secrets Manager.
