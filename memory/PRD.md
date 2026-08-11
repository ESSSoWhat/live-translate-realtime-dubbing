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

## Subscription-based usage tracking — VERIFIED (2026-07-24)
- Verified `app/services/usage.py` metering against a real Postgres seeded with `supabase_schema.sql` (tier_limits/users/usage_records).
- Test runner: `backend/tests/test_usage_tracking_integration.py` (standalone asyncio; repo has no pytest-asyncio). Run: `SUPABASE_DB_URL=postgresql://postgres:postgres@127.0.0.1:5432/lt_test PYTHONPATH=/app/backend python tests/test_usage_tracking_integration.py`. 10/10 pass.
- Covered: per-tier quotas (free 1800s / starter 18000s / pro 54000s / early_adopters unlimited 2^31-1), all event types (dub/tts/stt/translate/clone), read-only check_quota (no record), unknown-event no-op, LookupError on missing user, record_usage accumulation, monthly period_reset_date (1st of next month), concurrency.
- Endpoints verified end-to-end (TestClient + auth override, local PG): GET /api/v1/user/usage and GET /api/v1/user/me return correct tier + used/limit + period_reset_date (200).
- BUG FOUND & FIXED (concurrency over-consumption): old `check_and_record_quota` read used-count via `LEFT JOIN usage_records` with `FOR UPDATE OF u` (locked only the users row). Under READ COMMITTED the joined usage row isn't re-read after the lock releases, so N concurrent same-user requests all read used=0 and over-consumed (test recorded 2000s on a 1800s cap). Rewrote to increment-then-check atomically via `INSERT ... ON CONFLICT DO UPDATE ... RETURNING <col>`, comparing the returned new total to the tier limit and raising QuotaExceededError inside the txn to roll back. Row lock on the usage_records row (+ unique index on first insert) now serializes concurrent increments. `exc.used/limit/requested` semantics unchanged (used = total before the rejected request) so proxy.py 402 handler unaffected. 28 existing tests + 10 new all pass, ruff clean.

## ROOT CAUSE of failed deploys FOUND + FIXED: Cloud Build Dockerfile path (2026-07-27)
- Real deploy target is **Google Cloud Run via a Cloud Build trigger** (project livetranslate-488616, australia-southeast2, repo github.com/ESSSoWhat/live-translate-realtime-dubbing) — NOT Railway. The Railway URL in the Wix config is a STALE box. GitHub Actions ci.yml only tests (no deploy).
- Cloud Build error: `unable to prepare context: ... lstat /workspace/Dockerfile: no such file or directory` → the trigger builds a Dockerfile at REPO ROOT, but ours was at `backend/Dockerfile`. Every build failed → Cloud Run kept the old revision → new code never shipped.
- FIX (code-side, so the existing trigger works with no GCP console change): added root `/app/Dockerfile` (builds backend from repo-root context: `COPY backend/requirements.txt .` + `COPY backend/ .`, runs `uvicorn app.main:app` on $PORT:-8080) + `/app/.dockerignore` (excludes mobile/live-dubbing/wix-app/.git/etc. to keep context small). Does NOT affect Railway (uses backend/Dockerfile via railway.json). No Docker in pod so not built locally, but mirrors the proven backend/Dockerfile.
- REMAINING USER ACTIONS:
  1. Save to GitHub → push root Dockerfile + .dockerignore to the branch Cloud Build watches → trigger rebuilds → deploys to Cloud Run. (Alt: edit trigger → Dockerfile dir = backend/.)
  2. Set env on the Cloud Run service: PAYPAL_CLIENT_ID/SECRET/ENV/CURRENCY/WEBHOOK_ID/STARTER_PLAN_ID/PRO_PLAN_ID, LT_SYNC_SECRET, SUPABASE_URL/SERVICE_ROLE_KEY/DB_URL, ELEVENLABS_API_KEY.
  3. CLIENT URL MISMATCH: Wix BACKEND_URL (and Flutter/desktop configs) still point to the stale Railway URL — must be repointed to the Cloud Run service URL. (Agent can update these once user provides the Cloud Run URL.)
  4. PayPal webhook → point at https://<cloudrun>/api/v1/paypal/webhook.

## Deploy-readiness CONFIRMED; blocker is Railway↔GitHub wiring (2026-07-27)
- Ruled out all code-side deploy causes: code compiles; no hardcoded secrets; env-driven; requirements.txt complete for all new imports (email-validator, asyncpg, supabase 2.31, structlog, httpx, pydantic, fastapi, uvicorn[standard], pyjwt[crypto]); Procfile + nixpacks start cmd bind to $PORT; routers registered; verified E2E vs real Supabase.
- After user clicked "Save to GitHub" + "deploy" twice, live Railway STILL serves old build (wix/sync says WIX_SYNC_SECRET; paypal/analytics 404). => new commit not reaching the repo/branch Railway watches, OR auto-deploy off, OR Save-to-GitHub pushed to a different repo. This is user-side Railway/GitHub config; cannot be fixed from the pod (no git remote here, no Railway access).
- Resolution handed to user: verify GitHub repo has backend/app/routers/paypal.py on the branch Railway deploys; confirm Railway Source repo+branch match; trigger/enable Railway deploy; set env vars. Tell-tale of success: wix/sync switches WIX_SYNC_SECRET → LT_SYNC_SECRET and /api/v1/paypal/config → 200.

## BLOCKER: Railway deployment is STALE (2026-07-27)
- Probed live Railway backend `https://livetranslatedubtool-production.up.railway.app`: `/health`→200 but `/api/v1/paypal/*`→404, `/api/v1/analytics/*`→404, and `/api/v1/billing/wix/sync`→503 "Set WIX_SYNC_SECRET" (OLD name). => deployed build predates the PayPal integration AND the WIX_SYNC_SECRET→LT_SYNC_SECRET rename. A live PayPal webhook test is impossible until redeploy (PayPal would hit /api/v1/paypal/webhook → 404).
- deployment_agent: code compiles, no hardcoded secrets, env-driven (deploy-ready for Railway). Its MongoDB/ML/frontend "blockers" are Emergent-platform-specific and NOT applicable — user hosts on Railway (Postgres/Supabase supported).
- USER ACTION to unblock live test:
  1. Redeploy current /app codebase to Railway (Save to GitHub → Railway redeploy).
  2. Set Railway env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL, LT_SYNC_SECRET, ELEVENLABS_API_KEY, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_ENV, PAYPAL_CURRENCY, PAYPAL_WEBHOOK_ID, PAYPAL_STARTER_PLAN_ID, PAYPAL_PRO_PLAN_ID.
  3. PayPal dashboard → add webhook → https://<railway>/api/v1/paypal/webhook (events: BILLING.SUBSCRIPTION.ACTIVATED/CANCELLED/EXPIRED/SUSPENDED/UPDATED, PAYMENT.CAPTURE.COMPLETED) → copy Webhook ID into PAYPAL_WEBHOOK_ID.
  4. Verify: GET /api/v1/paypal/config → {configured:true,...}. Then approve a REAL sandbox subscription (dashboard "send test" often has no custom_id → 200 but no tier flip). Confirm tier flips via Supabase or GET /api/v1/paypal/subscription.

## Schema applied + FULL webhook dedupe verified in production (2026-07-27)
- User ran the updated schema. Confirmed `webhook_events` + `nudge_events` now EXIST in live Supabase.
- Full end-to-end webhook test against REAL Supabase (signature/paypal_configured mocked, everything else live) — ALL PASS:
  1. Fire event E1 (BILLING.SUBSCRIPTION.ACTIVATED, email|pro) → user provisioned tier=pro.
  2. Tampered tier→free in DB, refired SAME event id → response `{duplicate:true}`, tier stayed `free` (dedupe works: retry did NOT re-provision). **DEDUPE PASS**
  3. Fired new event id E2 → not duplicate → tier back to pro. **NON-DUP PASS**
  4. `POST /analytics/nudge` → 201 recorded (nudge_events write works).
  5. All test rows cleaned up (users + webhook_events + nudge_events).
- The complete PayPal → provision → tier-flip pipeline, retry-idempotency, and nudge analytics are now proven in production. REMINDER: user should rotate the service-role key that was shared.

## Real Supabase tier-write VERIFIED + webhook hardened (2026-07-26)
- Used the service-role key (`sb_secret_…` new format — works with supabase-py 2.31) to verify the exact code the PayPal webhook runs, against the LIVE Supabase (djjmuvzwjapkeydqdgbu):
  1. `provision_or_update_user(email,'pro','active',subscription_id=...)` → user created, tier=pro, status=active, subscription_id + api_key stored ✅
  2. lower "starter active" event → stayed pro (no accidental downgrade, _max_tier) ✅
  3. cancel → tier=free, status=canceled ✅  4. test row deleted ✅
  (Key passed via env only, never written to disk; temp script deleted. User advised to ROTATE the service-role key.)
- PRODUCTION-SAFETY FIX: `webhook_events` & `nudge_events` tables DON'T exist in Supabase yet (user hasn't run updated schema). The idempotency claim wrote to webhook_events BEFORE provisioning → would have 500'd every PayPal webhook on deploy. Made `_claim_webhook_event` degrade gracefully (on any store error → log + process, dedupe becomes best-effort) and `analytics.record_nudge` best-effort. Added regression test `TestClaimWebhookDegrades`. Backend suite now **50 passing, ruff clean** (my files; also cleaned pre-existing unused `# noqa: B008` that newer ruff flags for FastAPI Depends/Header defaults).
- ACTION for user: run updated `supabase_schema.sql` on Supabase to enable idempotency dedupe + nudge analytics (webhooks work regardless now, just without dedupe until then).

## Switch Plan In-App — PayPal subscription revise (2026-07-25, part 4)
- Backend: `POST /api/v1/paypal/subscription/revise` (authed) switches an existing subscription to a new plan (Starter→Pro) via PayPal `/v1/billing/subscriptions/{id}/revise`; returns `approve_url` when PayPal needs buyer approval (price increase). Added `_tier_for_plan_id` + handle `BILLING.SUBSCRIPTION.UPDATED` in the webhook (resolves new tier from resource.plan_id, provisions it). Idempotency + release still apply.
- Flutter: `ManagePlanScreen` now shows "Upgrade to Pro" for active Starter subscribers → calls revise, launches approve_url via url_launcher; "See all plans" still routes to paywall. api_client got `reviseSubscription`.
- Tests: +6 (revise: approve_url/404/400/auth; UPDATED webhook plan→tier). Backend suite now **49 tests passing, ruff clean**.
- Note: downgrade via revise keeps the higher tier while active (provision uses _max_tier) — intentional for now; scope was Starter→Pro upgrade.
- STILL BLOCKED ON USER: service-role key/SUPABASE_DB_URL (real tier-write verify) and nudge stats need live traffic.

## Manage-Plan (cancel) + Nudge A/B test (2026-07-25, part 3)
**Feature 4 — Cancel/Manage plan (backend tested, clients wired):**
- `provision_or_update_user(email, tier, status, subscription_id=None)` now persists the PayPal subscription id; webhook stores `resource.id` on BILLING.SUBSCRIPTION.ACTIVATED (users.subscription_id already existed in schema).
- New authed endpoints in paypal.py: `GET /api/v1/paypal/subscription` (tier + live PayPal status/next_billing) and `POST /api/v1/paypal/subscription/cancel` (calls PayPal cancel, sets subscription_status='canceled', keeps tier until PayPal ends cycle / webhook downgrades). 404 if no subscription_id.
- Flutter: `ManagePlanScreen` (lib/screens/manage_plan_screen.dart) shows plan + Cancel (with confirm) + Change/Upgrade → paywall; linked from settings_screen. api_client got getSubscription/cancelSubscription.

**Feature 3 — Upgrade-nudge A/B test (backend tested, clients wired):**
- New `analytics.py` router: `POST /api/v1/analytics/nudge` (records {variant a|b, action shown|clicked, feature, surface}) and admin `GET /api/v1/analytics/nudge/stats` (conversion per variant, admin secret = LT_SYNC_SECRET). New `nudge_events` table in supabase_schema.sql.
- Two copies: variant 'a' = "upgrade for more", variant 'b' = "you're about to run out". Variant assigned deterministically by hashing the user's email (stable per user; no new deps).
- Flutter usage_card + Wix paypal-page: pick variant from email, render matching copy, fire 'shown' on display and 'clicked' on upgrade/paypal-button tap. Desktop keeps single-copy nudge (smaller free-user surface).

Backend suite now **44 tests passing, ruff clean**. NEW schema needs applying on Supabase: `webhook_events`, `nudge_events` tables (run supabase_schema.sql). Clients verified via node/py static checks only — need on-device confirmation.

STILL BLOCKED ON USER: (1) service-role key / SUPABASE_DB_URL to verify the real tier write; (2) interactive PayPal approval of the sandbox subscription.

## Sandbox PayPal E2E verified + webhook idempotency + 80% usage nudge (2026-07-25, part 2)
**Sandbox PayPal API — REAL objects created (working creds: Client ID `BAA6_cVm8Twng…`, secret `EG3W3wwf…`; the `A…`/NVP values user pasted were wrong type):**
- Token OK, product `PROD-6GB36944R5303312P`, plans ACTIVE: starter `P-21T5856284475072KNJSMQ2Q`, pro `P-0S757496WR660225NNJSMQ2Q`; subscription `I-23A8AXCV00SF` (APPROVAL_PENDING) + one-time order `1VE518422M079354S` (CREATED), both with approve_urls. Proves the exact backend code the client buttons call works against real PayPal sandbox.
- Supabase project reachable (djjmuvzwjapkeydqdgbu) and schema LIVE — `tier_limits` returns correct rows via the publishable/anon key. User only supplied the ANON key, so the actual tier WRITE (provision_or_update_user uses SERVICE ROLE) is still NOT verified against their real DB — needs SUPABASE service_role key or SUPABASE_DB_URL.

**Webhook idempotency (backend, tested):** Added `webhook_events` table (unique event_id) to supabase_schema.sql. `paypal.py` now claims each event via upsert(ignore_duplicates, on_conflict=event_id) BEFORE provisioning → duplicate/retry returns `{received, duplicate:true}` and skips; on provisioning failure the claim is released so PayPal can retry. New regression test `test_duplicate_event_is_ignored`. Backend suite now 33 passing, ruff clean. Verified `INSERT ... ON CONFLICT DO NOTHING RETURNING` semantics (first→row, retry→empty).

**80% usage nudge (all clients):**
- Flutter `lib/widgets/usage_card.dart`: shows a highlighted banner + "Upgrade" button (→ PaywallScreen) when peak metered usage (minutes/TTS/translation) ≥80% (skips unlimited tiers).
- Wix `paypal-page.js`: `#upgradeNudge` element shows "You've used X% of your <feature> — upgrade" when peak ≥80%.
- Desktop `usage_meter.py`: usage label switches to "You've used X% of your minutes — upgrade" (accent color) at ≥80% when the tier can still upgrade.

**Runbook plan IDs (sandbox) for Railway:** PAYPAL_STARTER_PLAN_ID=P-21T5856284475072KNJSMQ2Q, PAYPAL_PRO_PLAN_ID=P-0S757496WR660225NNJSMQ2Q (only valid for that sandbox app; re-run setup-plans for live).

## PayPal live-smoke attempt + CRITICAL webhook bug fix (2026-07-25)
- User provided PayPal creds for a "sandbox" smoke test. They authenticate against the LIVE endpoint (api-m.paypal.com 200) and FAIL on sandbox (401 invalid_client) → they are **LIVE credentials**. Did NOT create any live subscriptions/orders/plans (would be real billing). Deleted the temp script holding the keys.
- CRITICAL BUG FOUND & FIXED (would 500 the webhook AFTER a real charge → PayPal retry storm): structlog reserves the `event` kwarg for the log message. `app/routers/paypal.py` webhook used `logger.info(..., event=event, ...)` (3 spots) and `app/routers/billing.py` Qonversion webhook used `logger.info(..., event=event_name)` (2 spots) → `TypeError: got multiple values for argument 'event'`. Renamed kwarg to `event_type`/`event_name`. The 28 existing tests missed it because the webhook success path was never exercised (only the 503-unconfigured path).
- Verified webhook tier-flip logic end-to-end locally (signature + provision mocked): ACTIVATED→provision(email,'pro','active'), PAYMENT.CAPTURE.COMPLETED→('...','early_adopters','active'), CANCELLED→('...','free','canceled'), bad-signature→400 no provisioning.
- Added regression tests: tests/test_paypal_endpoints.py::TestPaypalWebhookProvisioning (4 tests). Full suite now 32 passing, ruff clean.
- STILL PENDING for a true end-to-end: (1) SANDBOX PayPal creds to safely create a real subscription/order (or explicit consent to use LIVE); (2) the actual tier write goes through provision_or_update_user→live Supabase (needs their DB) — mocked here.

## PayPal client wiring + usage dashboards (2026-07-24)
Wired all three clients to the live PayPal endpoints and surfaced live usage. Backend unchanged this round (endpoints already tested, config endpoint verified 200 `configured:false`).
- **Wix Velo** (NEW): `wix-app/velo-pages/paypal.web.js` (backend module: getPayPalConfig, createPayPalSubscription starter/pro, createPayPalOrder + capturePayPalOrder for one-time early_adopters, getUsageForMember via LT_SYNC_SECRET→api-key→/user/me) + `wix-app/velo-pages/paypal-page.js` (page code: buttons #paypalStarterBtn/#paypalProBtn/#paypalEarlyBtn redirect to PayPal approve_url; on return with ?token= captures the order; usage panel #usageTier/#usageDubbing/#usageTts/#usageDubbingBar). Both pass `node --check`.
- **Flutter**: `api_client.dart` added getUsage, getPayPalConfig, createPayPalSubscription, createPayPalOrder. NEW `lib/widgets/usage_card.dart` (quota bars per feature from GET /user/usage, added to home_screen). `paywall_screen.dart` added "or pay on the web" PayPal buttons (Starter/Pro subscriptions + Early Adopters one-time) that launch approve_url via url_launcher (already a dep); shown only when /paypal/config configured. NOTE: not compiled (no Flutter SDK in pod). CAVEAT: PayPal for digital goods may violate Apple/Google in-app-purchase policy — kept behind config flag as a web alternative to Qonversion IAP.
- **Desktop (PyQt)**: NEW `services/paypal_checkout.py` (sync httpx: fetch_config, create_subscription, create_order) + `gui/widgets/paypal_dialog.py` (PayPalCheckoutDialog: plan combo + email + background QThread → opens approve_url in browser). Wired into `main_window.py` Account menu ("Upgrade with PayPal…") → `_open_paypal_checkout`. Usage dashboard ALREADY existed (UsageMeterWidget). py_compile OK; new files ruff-clean (pre-existing ruff nits in main_window unrelated).
- **Railway env (USER action, not code)**: user must set PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_ENV, PAYPAL_CURRENCY, then POST /paypal/admin/setup-plans (X-Admin-Secret=LT_SYNC_SECRET) → set returned PAYPAL_STARTER_PLAN_ID/PAYPAL_PRO_PLAN_ID + PAYPAL_WEBHOOK_ID. Plus LT_SYNC_SECRET + SUPABASE_DB_URL for sync/metering.

## ✅ MOCK LIVE PAYMENT TEST PASSED + test data cleaned (2026-06 fork)
- User asked for a mock payment (avoid real 9.99 AUD charge in live mode). Created live Starter subscription I-WDT4MJN81N43 (APPROVAL_PENDING, no charge) for live-test@livetranslate.net.
- Simulated BILLING.SUBSCRIPTION.ACTIVATED faithfully: provisioned starter via app's real provision_or_update_user (wix/sync starter plan) then set subscription_id=I-WDT4MJN81N43 + subscription_status=active via direct DB (pooler). Verified: public.users row = {tier:starter, subscription_status:active, subscription_id:I-WDT4MJN81N43, has_api_key:true}.
- End-to-end confirmed: GET /user/usage as that member → tier:starter with STARTER limits (dub 18000, tts 500000, stt 18000, trans 500000, clones 5) + real period_reset_date 2026-09-01. Only the PayPal charge + signed webhook delivery were mocked; all downstream (provision → DB → tier limits → usage endpoint) is real.
- schema note: two users tables — public.users (app: id, supabase_uid, email, api_key, tier, subscription_status, subscription_id, stripe_customer_id) + auth.users (Supabase auth).
- CLEANUP DONE: deleted test users live-test@livetranslate.net + wix-e2e-test@livetranslate.net from public.users (DELETE 2, DB clean). Pending subs I-WDT4MJN81N43 + I-N6XFU6WWPY8B auto-expire (never approved, no charge).

## ✅✅ USAGE TRACKING NOW LIVE (2026-06 fork) — SUPABASE_DB_URL fixed = pooler host aws-1
- Root cause of the long loop: user repeatedly set SUPABASE_DB_URL to the DIRECT host (db.djjmuvzwjapkeydqdgbu.supabase.co:5432) which is IPv6-only → Railway (no IPv6 egress) → "[Errno 101] Network is unreachable". Also earlier the var was missing entirely, and Raw Editor split a params-paste into stray port/database/user vars.
- FIX: must use the POOLER (IPv4). Probed from pod: aws-0-ap-southeast-1 → "tenant/user not found"; aws-1-ap-southeast-1 → "password auth failed" (=correct host). Project is on **aws-1**, not aws-0.
- VERIFIED working string (tested from pod: select 1 + tier_limits=4 rows): postgresql://postgres.djjmuvzwjapkeydqdgbu:<DBPASS>@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres  (user MUST be postgres.<ref>, host pooler.supabase.com, port 6543).
- After user set it: GET /api/v1/user/usage → period_reset_date 2026-09-01 (real query, not 08-31 fallback). Usage metering LIVE.
- ⚠️ SECURITY: DB password (65326845621548952344564) now exposed in chat too (plus PayPal secret + LT_SYNC_SECRET earlier). User declined rotation. Residual risk acknowledged.
- CLEANUP pending: delete Railway stray vars port/database/user + the var literally named "BAAmQF18..." (PayPal client id used as a var name) + unused NEXT_PUBLIC_/SUPABASE_PUBLISHABLE/SECRET/JWKS; delete test user wix-e2e-test@livetranslate.net.

## Usage tracking /user/me + /user/usage 500 on Railway — NOT verified working (2026-06 fork)
- With the wix-e2e-test api_key (APmn-5Rw…, user 05d6519a-…, tier pro), GET /api/v1/user/me and /api/v1/user/usage both return 500. Auth works (no 401) → failure is in get_usage_snapshot (asyncpg / SUPABASE_DB_URL layer). Root cause one of: SUPABASE_DB_URL unset, wrong (bad pw/host), or usage.py pooler fix not deployed. CANNOT confirm without Railway logs/access; user skipped the clarify question.
- CODE (needs push): added _usage_or_default() in user.py — /me and /usage now fall back to _default_usage(tier) (zeroed, tier-correct limits) on any DB error instead of 500, so profile endpoints never hard-crash (mirrors auth.py login fallback). Logs a warning. 50 tests pass. NOTE: this MASKS metering failure for display; real quota enforcement still needs SUPABASE_DB_URL working.
- USER TODO: set SUPABASE_DB_URL (transaction pooler URI + db password) on Railway + Save to GitHub (pushes usage.py _clean_dsn/statement_cache_size fix AND user.py fallback) → then re-test /user/usage: real numbers = works; all-zeros = fallback = DB still not wired (check Railway log for asyncpg error).
- ruff: project has NO [tool.ruff] config → default ruff flags B008 (FastAPI Depends-in-defaults) codebase-wide; pre-existing, not a blocker (CI runs pytest only, not ruff).

## 🔴→🟢 CRITICAL: Supabase env NOT set on Railway → FIXED + Wix E2E VERIFIED (2026-06 fork)
- Wix sync + api-key + PayPal webhook tier writes all 503'd: "Supabase not configured" — Railway backend had NO SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY. (Auth passed = LT_SYNC_SECRET correct; only Supabase client unconfigured.) This ALSO meant the PayPal webhook would have silently failed to flip tiers (same supabase client) — a hidden prod bug caught before spending money.
- Diagnosis took several tries because user set PayPal plan-ID vars (config showed them = deploy current) but not the Supabase pair. Guided user to Supabase Settings → API Keys → service_role/secret (sb_secret_, NOT publishable sb_publishable_) + SUPABASE_URL=https://djjmuvzwjapkeydqdgbu.supabase.co. Also clarified the Supabase "Connect" modal: Direct → Connection string tile = SUPABASE_DB_URL.
- After user set them: POST /billing/wix/sync (plan_name Pro) → 200 {tier:pro, user_created:true}; POST /auth/api-key → 200 {api_key APmn-5Rw…, user_id 05d6519a-a635-4728-ae4c-5e7ea69dbc14, tier:pro}. FULL Wix pipeline proven live against real Supabase.
- CODE HARDENING (usage.py, pushed): added _clean_dsn() to strip `pgbouncer=true` from SUPABASE_DB_URL + create_pool(statement_cache_size=0) so asyncpg works with Supabase transaction pooler. 50 tests pass.
- CLEANUP for user: delete test row wix-e2e-test@livetranslate.net from Supabase users table.
- STILL PENDING: SUPABASE_DB_URL (usage tracking) — verify user set it; real PayPal charge→webhook→tier-flip (user opted out of 9.99 AUD).

## Live Starter smoke test = DRY-RUN PASS (2026-06 fork)
- Config fully populated: starter_plan_id P-8FC35096LG2298041NJ5GUPI, pro_plan_id P-4NR95814GS3797306NJ5GUPI, configured:true, env LIVE.
- POST /api/v1/paypal/subscriptions {email:thesonluu@gmail.com, tier:starter} → 200, subscription_id I-N6XFU6WWPY8B, status APPROVAL_PENDING, valid www.paypal.com approve_url. Proves live checkout pipeline E2E up to approval. NO charge (never approved → auto-expires; PayPal cancel API only works on active subs so nothing to cancel).
- Full real-charge test (approve → webhook → tier flip → verify Supabase users.tier=starter → cancel) NOT run — user did not opt into the 9.99 AUD live charge. Webhook+provision logic already proven against real Supabase in prior testing (mocked signature).
- User DECLINED rotating the exposed PayPal Client Secret + LT_SYNC_SECRET (both were pasted in chat). Residual risk acknowledged; their call.

## PayPal LIVE credentials confirmed working (2026-06 fork)
- config → configured:true, env LIVE, currency AUD, client_id set.
- setup-plans succeeded (HTTP 200) = live OAuth against api-m.paypal.com works. Created LIVE product PROD-0BY98957PB715064A; plans starter P-8FC35096LG2298041NJ5GUPI (9.99 AUD/mo), pro P-4NR95814GS3797306NJ5GUPI (24.99 AUD/mo).
- User needs to set PAYPAL_STARTER_PLAN_ID + PAYPAL_PRO_PLAN_ID on Railway, create Live webhook (6 events) → PAYPAL_WEBHOOK_ID, then live Starter smoke test.
- ⚠️ SECURITY: user pasted PayPal Client Secret AND LT_SYNC_SECRET (A5x3Woq…) in chat → both exposed. Advised: rotate PayPal secret (Generate New Secret) and rotate LT_SYNC_SECRET in BOTH Railway and Wix Secrets Manager (must match). To be done after smoke test.

## ✅ RAILWAY DEPLOY VERIFIED LIVE (2026-06 fork) — new build serving, clients already correct
- User deployed to Railway; URL is the SAME domain as before: `https://livetranslatedubtool-production.up.railway.app`. Because the domain is unchanged, ALL clients (Wix api-key.web.js/paypal.web.js/sync.web.ts, desktop settings.py, mobile) ALREADY point to it → NO repointing needed.
- Curl-verified the NEW build is live (was stale/404 before): /health 200; / 200; /api/v1/paypal/config **200** {configured:false}; /api/v1/analytics/nudge/stats 401 "Invalid admin secret"; /api/v1/paypal/subscription 401; /api/v1/billing/wix/sync wrong-secret **401 "Invalid Wix sync secret"** (proves new code + LT_SYNC_SECRET is set — old build returned 503 "Set WIX_SYNC_SECRET").
- REMAINING: PayPal env vars NOT set (configured:false). User chose LIVE env. Gave full step-by-step: get live Client ID/Secret (developer.paypal.com → Live), set PAYPAL_CLIENT_ID/SECRET/ENV=live/CURRENCY=AUD on Railway, POST /paypal/admin/setup-plans (X-Admin-Secret=LT_SYNC_SECRET) → set PAYPAL_STARTER_PLAN_ID/PRO_PLAN_ID, create Live webhook → https://livetranslatedubtool-production.up.railway.app/api/v1/paypal/webhook (6 events) → PAYPAL_WEBHOOK_ID.
- LIVE PRICES (real money): starter 9.99 AUD/mo, pro 24.99 AUD/mo, early_adopters 149.00 AUD one-time. Smoke-test recommendation: approve Starter (9.99), confirm tier flip in Supabase, cancel.
- NEXT: user sets PayPal vars → re-check config → configured:true → live subscription smoke test → confirm tier flip.

## Railway chosen as deploy target (2026-06 fork) — railway.json switched to Dockerfile builder
- User picked Railway over GCP Cloud Build (GCP kept failing with "repository not found" = broken 2nd-gen GitHub connection, a user-side GCP console fix; web-searched + gave reconnect checklist).
- ROOT CAUSE of earlier Railway flakiness identified: `backend/package.json` declares a Node devDependency (`supabase` CLI) → Railpack/Nixpacks detects a Node app instead of Python. FIX: `backend/railway.json` builder changed RAILPACK → DOCKERFILE (`dockerfilePath: "Dockerfile"`) so Railway builds the proven `backend/Dockerfile` (binds $PORT). Valid JSON confirmed.
- DEPLOY STEPS given to user: Save to GitHub → Railway New Project from repo → **Root Directory = backend** (critical) → set env vars (BACKEND_ENV=production, BACKEND_CORS_ORIGINS=https://www.livetranslate.net, SUPABASE_URL/SERVICE_ROLE_KEY/DB_URL, ELEVENLABS_API_KEY, LT_SYNC_SECRET, PAYPAL_*) → Generate Domain → verify /health + /api/v1/paypal/config.
- PENDING: user deploys + shares live Railway URL → then repoint Wix (api-key.web.js:22, paypal.web.js:23, sync.web.ts:16), desktop (settings.py:204 backend_base_url), mobile (api_config.dart) from stale https://livetranslatedubtool-production.up.railway.app to the new URL.

## GitHub CI backend tests FIXED (2026-06 fork) — code IS now in ESSSoWhat/live-translate-realtime-dubbing
- "Save to GitHub" confirmed working — CI ran on commit "Auto-generated changes #54". The GCP "File ... not found" error = code not yet on the branch the Cloud Build trigger watches / repo connection (user-side; guidance given via support_agent: push to the correct repo+branch first, THEN trigger build, or use Emergent's Deploy button).
- GitHub Actions "Backend tests" job failed on 4 tests (all secret-auth): TestNudgeStats::test_stats_computes_conversion (401≠200), TestPaypalAdminSetupPlans::test_setup_plans_with_correct_admin_returns_503 (401≠503), TestWixSyncRegression wrong/missing-secret (503≠401).
- ROOT CAUSE: tests hardcode X-Admin-Secret="test-secret-123" and rely on LT_SYNC_SECRET being configured. Local backend/.env supplies it (pass), but CI's ci.yml env block does NOT set LT_SYNC_SECRET and .env is gitignored → empty secret → admin guard 401 / wix-sync 503. Not a code bug — an env-dependent test.
- FIX (backend/tests/conftest.py): `os.environ.setdefault("LT_SYNC_SECRET", "test-secret-123")` so the suite is self-contained regardless of .env. Verified 50 passed both WITH .env and under CI-sim (mv .env aside + `env -u LT_SYNC_SECRET`).
- ALSO: `collect_ignore = ["test_usage_tracking_integration.py"]` in conftest. That file is a standalone asyncio script (needs real Postgres). CI pins pytest==8.3.4 (async tests → skipped) but the pod has pytest 9.1.1 (async tests → FAILED). Excluding it from collection future-proofs against a pytest 9 bump. CI does NOT run ruff (only pytest), so pre-existing ruff nits don't block.
- NEXT: user must "Save to GitHub" again (push the conftest fix) → CI backend job goes green.

## Deploy artifacts re-verified (2026-06 fork) — blocker is USER git push, not code
- Re-verified root `/app/Dockerfile`
- Re-verified root `/app/Dockerfile`: copies `backend/requirements.txt` then `backend/` → `app.main:app` resolves; CMD binds $PORT:-8080. `.dockerignore` excludes mobile/live-dubbing/wix-app/.git. `backend/app.main:app` imports cleanly (46 routes), requirements.txt complete.
- Only uncommitted change: `backend/.env.example` WIX_SYNC_SECRET→LT_SYNC_SECRET rename (doc only, harmless).
- NO cloudbuild.yaml in repo — GCP trigger builds root Dockerfile with repo-root context (matches our setup).
- ACTION SEQUENCE for user to unblock (order matters):
  1. Click "Save to GitHub" in chat → pushes root Dockerfile + .dockerignore to the branch Cloud Build watches.
  2. THEN re-trigger the Cloud Build in GCP console (or let auto-trigger fire on push).
  3. Grab the Cloud Run service URL → agent curls /health + /api/v1/paypal/config to confirm new image.
  4. Agent updates BACKEND_URL in Wix (api-key.web.js + sync.web.ts), Flutter (API_BASE_URL), desktop (settings.py:204) to the Cloud Run URL.
  5. Set Cloud Run env vars (PAYPAL_*, LT_SYNC_SECRET, SUPABASE_*, ELEVENLABS_API_KEY) + PayPal webhook → https://<cloudrun>/api/v1/paypal/webhook.

## Sync secret renamed WIX_SYNC_SECRET -> LT_SYNC_SECRET (2026-07)
- Backend config field is now lt_sync_secret with validation_alias=AliasChoices("LT_SYNC_SECRET","WIX_SYNC_SECRET") — canonical LT_SYNC_SECRET, legacy WIX_SYNC_SECRET still accepted.
- Updated refs in config.py, billing.py, auth.py, paypal.py, main.py; docs (PRODUCTION_READINESS.md, WIX_SSO_SETUP.md, BACKEND_URL.md, velo README) + .env.example.
- Both Wix Secrets Manager AND backend env now use the SAME name: LT_SYNC_SECRET. 28/28 tests pass, alias verified.
