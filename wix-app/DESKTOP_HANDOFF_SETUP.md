# Desktop sign-in — backend handoff setup

Wix cannot redirect back to `http://localhost`, so the desktop app no longer waits
for a localhost callback. Instead it uses a **device-code style handoff**:

1. The desktop app generates a one-time `code` and opens the browser to livetranslate.net (carrying `?handoff=<code>`).
2. You log in on the website. The `/api-key` page posts your API key to the backend, keyed by that `code`.
3. The desktop app polls `GET /api/v1/auth/desktop-handoff?code=<code>` and receives the key **once** (single-use, expires in 10 min).

No localhost redirect anywhere.

---

## What you need to do (3 steps)

### Step 1 — Supabase: create the handoff table (run once)

Supabase Dashboard → **SQL Editor** → New query → paste the contents of
`backend/migrations/002_desktop_handoffs.sql` → **Run**.

It creates `public.desktop_handoffs` and is safe to run multiple times.

### Step 2 — Railway: redeploy the backend

Push the updated `backend/` to Railway (adds two endpoints in
`backend/app/routers/auth.py`):

- `POST /api/v1/auth/desktop-handoff` — Wix stores the key here (needs `LT_SYNC_SECRET`).
- `GET  /api/v1/auth/desktop-handoff?code=...` — desktop polls here (no secret; the code is the credential).

Verify after deploy (should return `{"status":"pending"}`):

```
curl "https://livetranslatedubtool-production.up.railway.app/api/v1/auth/desktop-handoff?code=testtesttesttesttest"
```

### Step 3 — Wix: publish 3 files, then Publish the site

Replace the code in your Wix editor with these repo copies, then click **Publish**:

| Repo file | Where it goes in Wix |
| --- | --- |
| `wix-app/velo-pages/api-key.web.js` | Backend file: `backend/api-key.web.js` |
| `wix-app/velo-pages/app-auth-page.js` | Page `/app-auth` (public) code panel |
| `wix-app/velo-pages/api-key-page.js` | Page `/api-key` (members-only) code panel |

No new Wix elements are required (it reuses `#statusText`, `#apiKeyText`, `#copyButton`).

---

## Test it

1. Restart the desktop app.
2. Click **Sign in with livetranslate.net**.
3. Log in in the browser → the page shows
   *"✓ Signed in! You can return to the Live Translate app…"*.
4. The app auto-completes sign-in within a couple of seconds — no copy/paste.

If anything fails, the app still shows the **"Having trouble? Use API key"** manual
fallback, and the website still shows your key with a Copy button.

## Notes

- The legacy `redirect_uri` (localhost) path is kept for backward compatibility; the
  handoff path takes priority when both are present.
- `desktop_handoffs` rows are single-use and expire after 10 minutes. Old rows can be
  cleaned up with the optional `DELETE` statement at the bottom of the migration.
