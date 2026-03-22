# Wix SSO Setup — Live Translate Desktop App

This guide configures the Wix site (livetranslate.net) so the desktop app can complete sign-in via SSO.

## 1. API-key page — use only `api-key-page.js`

The route/page that serves your API Key page **must** use `api-key-page.js` exactly as-is:

- In Wix Editor: create a page with slug `api-key` (or your chosen path).
- Open the page code panel and **replace all existing code** with the contents of `api-key-page.js`.
- Remove any old custom logic; rely only on this file.
- Set the page to **Members Only** in page settings.

Ensure `api-key.web.js` is in your backend folder and the page imports from it.

## 2. Create the `/app-auth` public page

Add a **public** page (not Members Only) with slug `app-auth`:

1. Create a new page with slug `app-auth`.
2. Do **not** set it to Members Only — it must be public so it loads before any login redirect.
3. Paste the contents of `app-auth-page.js` into the page's code panel.
4. Publish.

This page receives `redirect_uri` and immediately redirects to `/api-key?redirect_uri=...`, preserving the param through the Wix login flow.

## 3. redirect_uri must point to `/app-auth`

Wherever you construct the OAuth/SSO entry URL (in the desktop app this is `get_wix_sso_entry_url()`):

- Use: `https://www.livetranslate.net/app-auth?redirect_uri=...` (or your actual domain).
- The desktop app already uses `/app-auth` by default via `LIVE_TRANSLATE_WIX_APP_AUTH_PATH`.
- If you support multiple environments, keep this environment-driven (e.g. `WEBSITE_URL + LIVE_TRANSLATE_WIX_APP_AUTH_PATH`).

## 4. Set `LIVE_TRANSLATE_WIX_APP_AUTH_PATH`

In the desktop app's deployment/environment:

```
LIVE_TRANSLATE_WIX_APP_AUTH_PATH=/app-auth
```

The desktop app defaults to `/app-auth`; set this explicitly only if you need to override. Redeploy so the value is picked up.

## 5. Add the fallback link on the api-key page

On the api-key page's markup (in Wix Editor):

1. Add a **Link** element.
2. Set its ID to `completeSignInLink`.
3. Initially hide it (or leave it empty; the page code will populate and show it).

`api-key-page.js` will show this link with text "Click here if the app didn't sign in" when the automatic redirect may have been blocked (e.g. popup blockers). The link’s `href` is set to the completion URL so the user can click to finish sign-in.

## 6. Backend config: BACKEND_URL and WIX_SYNC_SECRET

**BACKEND_URL** (in `api-key.web.js`):

- This must be the actual base URL of your FastAPI backend.
- Example: `https://api.livetranslate.app`.
- If APIs are at a different domain (e.g. `https://api.live-translate.com`), change the constant.
- All frontend calls to sync/auth use this URL.

**WIX_SYNC_SECRET**:

1. In Wix: open Secrets Manager and set `WIX_SYNC_SECRET` to a secure, random string.
2. In your backend environment, set the same value: `WIX_SYNC_SECRET=the-same-secret-value`.
3. Ensure prod/staging match their respective Wix configs — a mismatch breaks sync and API-key provisioning.

---

## Quick checklist

- [ ] api-key page uses only `api-key-page.js`, no old custom logic
- [ ] `api-key.web.js` is in backend folder; `BACKEND_URL` is correct
- [ ] Public page `/app-auth` created with `app-auth-page.js`
- [ ] Link element `completeSignInLink` added to api-key page (initially hidden)
- [ ] `WIX_SYNC_SECRET` set in Wix Secrets Manager and backend
- [ ] `LIVE_TRANSLATE_WIX_APP_AUTH_PATH=/app-auth` (or default) in desktop app env
- [ ] Site published after all changes
