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
- P0: ~~Confirm real Wix plan names and align mapping~~ DONE — `_WIX_PLAN_ID_TO_TIER` populated with user's confirmed plan GUIDs: 146fe70b→pro, fe160051→starter, 954bf0a7→early_adopters, 5f9d4418→free. Plan-ID match takes priority over name/keyword.
- P0 (config): matching `WIX_SYNC_SECRET` (Wix Secrets ↔ backend), correct `BACKEND_URL` in `api-key.web.js` + `sync.web.ts`, publish `/api-key` (Members) + `/app-auth` (public), run `supabase_schema.sql`.
- P1: ~~Fix `auth.py::_default_usage`~~ DONE. ~~Add `early_adopters` to `sync.web.ts` MemberInfo~~ DONE. ~~Decide Stripe path~~ DONE — Stripe HIDDEN: /plans, /checkout, /portal all 503 when unconfigured; no client calls them; upgrades route to Wix /upgrade.
- P1 (deploy): mobile release signing + dart-defines; desktop CI `FFMPEG_DIR`; set `BACKEND_ENV=production` + explicit `BACKEND_CORS_ORIGINS`.
- P2: ~~Migrate FastAPI `on_event` → lifespan~~ DONE (lifespan handler; startup log intact). Improve usage metering accuracy (remaining).

## Auth JWT verification (JWKS) — DONE & live-verified
- Supabase project uses NEW asymmetric JWT signing (ES256). Old code only verified legacy HS256 → web/OAuth logins would fail.
- Rewrote `app/dependencies.py::get_current_user` to use PyJWT `PyJWKClient` (cached JWKS, key rotation): verifies RS256/ES256 via JWKS, HS256 legacy fallback if `SUPABASE_JWT_SECRET` set, validates iss=`<url>/auth/v1` + aud=`authenticated`. API-key (Wix) path unchanged. Added `pyjwt[crypto]>=2.8.0` to requirements.txt.
- LIVE-VERIFIED against project: minted real ES256 token via admin.create_user+sign_in → `_verify_supabase_jwt` returns correct `sub`; tampered token rejected. Test auth users cleaned up.
- Regression tests added: `tests/test_jwt_verification.py` (8 tests — RS256 valid/tamper/wrong-issuer/expired via mocked JWKS + local RSA keypair, HS256 valid/no-secret, unsupported alg, JWT-shape). Full suite now 13 tests, ruff clean.
- NOTE: supabase-py 2.31 `sb.auth.admin.delete_user()` returns "User not allowed" with the new `sb_secret_` key (client sends a body GoTrue rejects); raw `DELETE /auth/v1/admin/users/{id}` works. Only affects register()'s best-effort rollback (already try/except-wrapped, non-blocking). Real auth users in project: son-luu@hotmail.com, son@livetranslate.net, thesonluu@gmail.com, sonsowhat@livetranslate.net.

## Verified via testing_agent (iteration_1, 16/17 then 17/17 after fixes)
- Wix sync: 401 on missing/wrong secret; reaches DB (503 Supabase-not-configured in sandbox) with correct secret via `X-Wix-Sync-Secret` OR `Authorization: Bearer`; 422 on invalid email. This confirms the user's "silent linking failure" = secret mismatch (401).
- Stripe hidden: `/billing/plans` and `/checkout` return 503 (never 500).
- Startup log prints active plan->tier mapping.
- FIXED (from test report): `get_current_user` now returns 401 (was 422) when Authorization header missing; added exception chaining in billing.py; removed pre-existing unused imports (proxy.py, dependencies.py).
- Added `backend/server.py` shim so the Emergent supervisor (`uvicorn server:app`) can run this repo (prod still uses `app.main:app`). Sandbox `backend/.env` has WIX_SYNC_SECRET=test-secret-123, Supabase intentionally blank.

## Credentials
No secrets committed (no backend `.env`; `.env.example` present). `.env` is gitignored. All keys must be set in deploy env / Wix Secrets Manager.
NOTE: Wix Secrets Manager forbids secret names starting with `wix` → the Wix secret is named `LT_SYNC_SECRET` (Velo reads `getSecret('LT_SYNC_SECRET')`); its value must equal the backend env `WIX_SYNC_SECRET`.

## Supabase (project ref djjmuvzwjapkeydqdgbu, region ap-southeast-1) — VERIFIED LIVE
- User adopted Supabase's NEW API key format (`sb_secret_`/`sb_publishable_`). supabase-py 2.10.0 rejected it (JWT-regex check). UPGRADED supabase==2.31.0 + pydantic==2.13.4 in requirements.txt (installable, 5 tests pass, ruff clean, imports OK).
- Proved end-to-end against LIVE prod Supabase: POST /billing/wix/sync (plan_id 146fe70b→pro) → 200 user_created tier=pro; POST /auth/api-key → 200 api_key+tier. Test users cleaned up.
- Backend env mapping: SUPABASE_SERVICE_ROLE_KEY = the `sb_secret_...` value (works with 2.31.0). SUPABASE_URL=https://djjmuvzwjapkeydqdgbu.supabase.co. Schema already applied (tier_limits seeded, matches code).
- STILL NEEDED for usage tracking: SUPABASE_DB_URL (asyncpg) — needs DB password; ap-southeast-1 pooler host. SUPABASE_JWT_SECRET only needed for Supabase email/password + OAuth login endpoints (new projects use asymmetric JWKS — those endpoints import-verified but NOT live-tested; Wix flow uses API keys, no JWT needed).
- S3 storage keys the user provided are NOT used by this backend (no object storage feature).
- ACTION: user must ROTATE the sb_secret_ key (pasted in chat) and remove leftover test users (test@example.com, test1@example.com, audit_run_test@example.com); son-luu@hotmail.com looks like a real member — left intact.
- Removed brittle testing-agent file tests/test_wix_billing_integration.py (asserted sandbox-only 503s, wrote to live DB, would break CI).

## Cloud Run deployment (project livetranslate-488616, australia-southeast2)
- FIXED: Dockerfile hardcoded `--port 8000` -> now `CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 2`. Cloud Run injects $PORT=8080; hardcoded port = failed deploy. Verified start command binds $PORT (/health 200 on 8080 and default).
- Set on Cloud Run: BACKEND_ENV=production, BACKEND_CORS_ORIGINS, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (new sb_secret_), WIX_SYNC_SECRET, SUPABASE_DB_URL, ELEVENLABS_API_KEY.
- After deploy, set the Cloud Run URL as the SINGLE backend URL for ALL clients: desktop settings.py:204, Wix BACKEND_URL (api-key.web.js + sync.web.ts), mobile API_BASE_URL. Desktop currently points to a dead Railway URL; Wix to api.livetranslate.net (no DNS record).

## Backend LIVE on Railway (2026-07)
- URL: https://livetranslatedubtool-production.up.railway.app — /health 200, / 200, billing/plans 503 (Stripe hidden OK).
- Fixed monorepo build: Railway Root Directory must = backend (Railpack failed at repo root due to package.json+pyproject conflict).
- All clients repointed to Railway URL: Wix api-key.web.js + sync.web.ts (was dead api.livetranslate.net), mobile doc comment; desktop settings.py:204 already correct.
- BLOCKER: WIX_SYNC_SECRET NOT set on Railway (wix/sync returns 503 not 401). Must set it (== LT_SYNC_SECRET in Wix) + SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL, ELEVENLABS_API_KEY, BACKEND_ENV=production, BACKEND_CORS_ORIGINS.

## PayPal integration (added 2026-07, coexists with Wix)
- Backend: app/routers/paypal.py + app/services/paypal_client.py (current PayPal REST via httpx; SDK deprecated). One-time Orders + recurring Subscriptions + verified webhook; on success calls shared billing.provision_or_update_user() to set Supabase tier + API key. Currency AUD. Env: LIVE.
- Config: PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_WEBHOOK_ID, PAYPAL_ENV, PAYPAL_CURRENCY, PAYPAL_STARTER_PLAN_ID, PAYPAL_PRO_PLAN_ID (all env, unset in sandbox).
- Endpoints (/api/v1/paypal): config, orders, orders/{id}/capture, subscriptions, webhook, admin/setup-plans (admin=WIX_SYNC_SECRET value).
- testing_agent iteration_2: caught CRITICAL latent bug (stdlib logging + structlog kwargs -> would 500 in prod AFTER charging). FIXED: both files now use structlog. Also reordered admin guard before config (401 for bad secret). 28/28 tests pass.
- TODO for user: set PayPal env vars on Railway; POST /paypal/admin/setup-plans (X-Admin-Secret=WIX_SYNC_SECRET) to create AUD plans -> put returned IDs in PAYPAL_STARTER_PLAN_ID/PAYPAL_PRO_PLAN_ID; create webhook -> PAYPAL_WEBHOOK_ID; wire Wix/mobile PayPal buttons to these endpoints. One-time price _ONE_TIME_PRICES early_adopters=149.00 AUD (edit to real price).
