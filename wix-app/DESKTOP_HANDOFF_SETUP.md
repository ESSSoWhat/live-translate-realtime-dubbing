# Desktop sign-in — backend handoff setup

Wix cannot redirect back to `http://localhost`, so the desktop app no longer waits
for a localhost callback. Instead it uses a **device-code style handoff**:

1. The desktop app generates a one-time `session_id` and opens the browser to livetranslate.net (carrying `?session_id=<id>`).
2. You log in on the website. The `/api-key` page posts your API key to the backend, keyed by that `session_id`.
3. The desktop app polls `GET /api/v1/auth/desktop-handoff/<session_id>` and receives the key **once**.

No localhost redirect anywhere.

---

## Good news: the backend is ALREADY deployed

The Railway backend already serves these endpoints (verified live):

- `POST /api/v1/auth/desktop-handoff` — body `{session_id, api_key}`, protected by `LT_SYNC_SECRET`.
- `GET  /api/v1/auth/desktop-handoff/{session_id}` — returns `{"ready": false}` until the key is posted, then `{"ready": true, "api_key": ...}`.

So you do **NOT** need to redeploy Railway or run the Supabase migration to make sign-in work.
`backend/migrations/002_desktop_handoffs.sql` is only needed **if** you later redeploy THIS repo's
backend (which uses a reliable DB-backed store).

---

## What you need to do: publish 2 Wix files + pull the desktop app

### Step 1 — Wix: publish, then Publish the site

Replace the code in your Wix editor with these repo copies, then click **Publish**:

| Repo file | Where it goes in Wix |
| --- | --- |
| `wix-app/velo-pages/api-key.web.js` | Backend file: `backend/api-key.web.js` |
| `wix-app/velo-pages/app-auth-page.js` | Page `/app-auth` (public) code panel |
| `wix-app/velo-pages/api-key-page.js` | Page `/api-key` (members-only) code panel |

No new Wix elements are required (it reuses `#statusText`, `#apiKeyText`, `#copyButton`).

### Step 2 — Desktop: pull the updated app

Save to GitHub → `git pull` on your machine (updates `login_dialog.py` + `settings.py`).

---

## Test it

1. Restart the desktop app.
2. Click **Sign in with livetranslate.net** — the app shows live status ("Opening your browser…" → "Waiting for you to sign in…").
3. Log in in the browser → the page shows *"✓ Signed in! You can return to the Live Translate app…"*.
4. The app auto-completes sign-in within a couple of seconds — no copy/paste.

If anything fails, the app still shows the **"Having trouble? Use API key"** manual
fallback, and the website still shows your key with a Copy button. There's also a
**Cancel** button during the wait so you can abort and retry.

## Fallback: redeploy this repo's backend (only if the live handoff is flaky)

The live backend's internal storage is not in this repo, so if seamless login is ever
unreliable (e.g. under multiple workers), redeploy `backend/` from this repo — it uses a
single-use, DB-backed store. First run `backend/migrations/002_desktop_handoffs.sql` in the
Supabase SQL Editor, then redeploy to Railway. Verify:

```
curl ".../api/v1/auth/desktop-handoff/testtesttesttesttest"   # -> {"ready":false}
```
