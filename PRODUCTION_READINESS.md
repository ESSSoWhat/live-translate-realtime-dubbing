# Live Translate — Production Readiness Report

_Audit date: June 2026. Scope: all components (backend, mobile, desktop, Wix site). Backend was run and verified in a sandbox._

## 0. Summary

The backend is in good shape: it boots cleanly, all 5 unit tests pass, health/root endpoints respond, and endpoints correctly return `503` when their dependency (Supabase / Stripe / Wix secret) is unconfigured. The architecture (Wix = source of truth for auth + subscriptions, backend issues per-user API keys, apps authenticate with `Authorization: Bearer <api_key>`) is sound.

The main thing blocking "finalize production" is **configuration + wiring of the Wix ↔ backend subscription sync**, plus a code bug in the Wix SSO entry page (now fixed). Details below.

---

## 1. Wix subscription linking (your blocker) — FIXED + CHECKLIST

### 1a. Bug fixed in this pass
`wix-app/velo-pages/app-auth-page.js` used `wixLocation.query`, but only `wixLocationFrontend` was imported. `wixLocation` is undefined → the `/app-auth` page threw a `ReferenceError` on load and never redirected to `/api-key`. This is the entry point of "Sign in with Wix", so SSO silently failed.
**Fix applied:** now uses `wixLocationFrontend.query`.

### 1b. Why subscription linking usually "gets stuck" — config, not code
The sync path is: Wix Velo → `POST /api/v1/billing/wix/sync` (+ `POST /api/v1/auth/api-key`), authenticated with `WIX_SYNC_SECRET`. Three settings must line up exactly or it fails **silently** (the web module returns `{received:false}` and the page just shows a generic error):

1. **`WIX_SYNC_SECRET` must match** in two places:
   - Wix Dashboard → Secrets Manager → **`LT_SYNC_SECRET`** (the Wix secret name must NOT start with `wix`, or Wix rejects it with "Some fields have invalid or missing information"; Velo reads it via `getSecret('LT_SYNC_SECRET')`)
   - Backend environment (`.env` / Railway variables) → `WIX_SYNC_SECRET`
   The **values** must match (names differ). A mismatch → backend returns `401` → `getApiKeyForMember` returns `{success:false}`.

2. **`BACKEND_URL` must be your real backend host.** It is hardcoded to `https://api.livetranslate.net` in:
   - `wix-app/velo-pages/api-key.web.js` (line 21)
   - `wix-app/live-translate-jsw/src/backend/sync.web.ts` (env `BACKEND_URL`, same default)
   If your FastAPI is deployed elsewhere (e.g. a Railway `*.railway.app` URL) and DNS for `api.livetranslate.net` isn't pointed at it, every Velo fetch fails. Either point DNS at the backend or change `BACKEND_URL` and republish the Wix site.

3. **The Wix pages must exist, be published, and use the exact slugs** the apps expect: `/api-key` (Members Only), `/app-auth` (public), and login/homepage code. If `/api-key` isn't published it 404s.

### 1c. Do-this checklist to finish subscription linking
- [ ] Deploy backend to a public HTTPS host; confirm `GET /health` returns `{"status":"ok"}`.
- [ ] Set backend env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_DB_URL`, `ELEVENLABS_API_KEY`, and a strong random `WIX_SYNC_SECRET`.
- [ ] Run `backend/supabase_schema.sql` in Supabase (creates `tier_limits`, `users`, `usage_records`, `user_voices` + seeds tiers).
- [ ] Point `api.livetranslate.net` DNS at the backend **or** update `BACKEND_URL` in `api-key.web.js` and `sync.web.ts`, then republish Wix.
- [ ] Store the identical `WIX_SYNC_SECRET` in Wix Secrets Manager.
- [ ] In Wix: create/publish `/api-key` (Members Only) with `api-key-page.js`, `/app-auth` (public) with `app-auth-page.js`, add `api-key.web.js` to the backend folder, add `login-page.js` + `homepage-sso.js`.
- [ ] Map your real Wix Pricing Plan names to tiers — see item 2 below.
- [ ] Smoke test: as a test member, load `/api-key` → API key shows; from the desktop app "Sign in with Wix" → app receives key.

---

## 2. Functionality gaps / inconsistencies to close

**P0**
- **Wix plan-name → tier mapping is guesswork.** `billing.py::_wix_plan_to_tier` matches literal strings like `"monthly language unlocked - pro tier"`, `"hobby tier"`, `"early adopters"`. If your actual Wix plan names differ, users sync to `free`. Confirm your real plan names and align this function (or send `tier` explicitly from Velo). This directly affects paid users getting the tier they paid for.

**P1**
- **Fallback usage limits don't match the schema.** `auth.py::_default_usage` uses starter=7200s (2 hr) and pro=36000s (10 hr), while `tier_limits` and `WIX_SYNC.md` say starter=18000s (5 hr) and pro=54000s (15 hr). These defaults are only used when the DB is unreachable, but they'd under-report limits to users. Align the numbers.
- **`sync.web.ts` MemberInfo tier type omits `early_adopters`** (`'free'|'starter'|'pro'`). Lifetime/early-adopter members synced through the JSW path can't be typed correctly. Add `early_adopters`.
- **Stripe path is effectively unused** (`stripe_price_id` is NULL in the seed, billing is Wix-driven). That's fine as a design choice, but `/billing/checkout` + `/billing/plans` will 400/return empty. Decide: keep Stripe as a real second path (fill price IDs) or hide it from the apps to avoid dead buttons.

**P2**
- STT/dubbing usage is **estimated** from audio size/bitrate heuristics and a "~15 chars/sec" rule for streaming. Acceptable for metering, but paid-tier accuracy may drift; consider metering from ElevenLabs' reported usage where available.

---

## 3. Deployment / CI readiness

- **CI exists** (`.github/workflows/ci.yml`, `release-android-reusable.yml`, `mlops-ci-cd.yml`) — backend pytest, website lint/build, Flutter analyze/test/build, and tagged desktop/Android release jobs. Good baseline.
- **Backend deploy** is Railway/Docker-ready (`Dockerfile`, `railway.json`, `nixpacks.toml`, `Procfile`). Verify all required env vars are set in the platform (Supabase x4, ElevenLabs, `WIX_SYNC_SECRET`).
- **CORS**: `main.py` already refuses `*` in production (good). Set `BACKEND_CORS_ORIGINS` to your real origins (`https://www.livetranslate.net`) and `BACKEND_ENV=production`.
- **Deprecation**: backend uses `@app.on_event("startup")` (deprecated in FastAPI). Non-blocking; migrate to lifespan handlers eventually.
- **Mobile**: needs signed release secrets in CI (`ANDROID_KEYSTORE_BASE64`, passwords, alias) and iOS cert/profile before store builds. Pass `--dart-define=API_BASE_URL=https://api.livetranslate.net/` for release. Qonversion + Google web client IDs must be set via dart-defines.
- **Desktop**: Windows installer build needs `FFMPEG_DIR` set in CI (the spec defaults to a local machine path — must be overridden on the runner).

---

## 4. Component-by-component status

| Component | State | Blocking items before prod |
|-----------|-------|----------------------------|
| Backend (FastAPI) | Boots, tests pass, endpoints behave | Set env vars; run schema; confirm Wix plan mapping |
| Wix site / Velo | Code present; SSO entry bug fixed | Publish pages w/ correct slugs; matching `WIX_SYNC_SECRET`; correct `BACKEND_URL` |
| Mobile (Flutter) | Config env-driven | Release signing secrets; dart-defines; store listings |
| Desktop (Windows) | Build spec present | CI `FFMPEG_DIR`; optional Inno Setup signing |
| Website pages (static) | `account/download/login/upgrade.html` present | Confirm they align with Wix production (Wix is the live site) |

---

## 5. Verified in this audit
- Backend imports and serves: `GET /health` → 200, `GET /` → 200.
- `POST /api/v1/billing/wix/sync` → `503` when `WIX_SYNC_SECRET` unset (correct guard).
- `GET /api/v1/billing/plans` → `503` when Supabase unset (correct guard).
- `pytest backend/tests` → 5 passed.
- Fixed `app-auth-page.js` undefined `wixLocation` reference.
