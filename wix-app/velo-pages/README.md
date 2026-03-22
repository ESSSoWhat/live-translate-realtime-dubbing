# Wix Velo Page: API key (slug `/api-key` or `/account/api-key`)

**Production:** [www.livetranslate.net/api-key](https://www.livetranslate.net/api-key) uses slug **`api-key`**. The app default `WIX_ACCOUNT_URL` matches that.

**Until a page exists and is published** at your chosen slug, that URL returns **404** in the browser. Add the page below and **Publish**.

This folder contains the Velo code for the API key page that enables SSO with the Live Translate desktop app.

## Files

| File | Purpose | Where to add |
|------|---------|--------------|
| `app-auth-page.js` | **Entry point for Sign in with Wix** (public page) | app-auth page code panel |
| `api-key-page.js` | API key page frontend (calls sync + getApiKey) | API key page code panel |
| `api-key.web.js` | Backend: `syncMemberToBackend`, `getApiKeyForMember` | `backend/` folder |
| `login-page.js` | Login page returnUrl support | Login page code panel (optional) |

## Setup Instructions

### Step 1: Enable Dev Mode

1. Open your Wix site in the Editor
2. Click **Dev Mode** → **Turn on Dev Mode**

### Step 2: Add the Backend Module

1. In the sidebar, expand **Backend & Public**
2. Click **+** next to **Backend**
3. Create a new file: `api-key.web.js`
4. Paste the contents of `api-key.web.js` from this folder

### Step 3: Add the Secret

1. Go to **Wix Dashboard** → **Secrets Manager**
2. Click **+ Store a Secret**
3. Name: `WIX_SYNC_SECRET`
4. Value: Same as your backend's `WIX_SYNC_SECRET` env variable
5. Click **Save**

### Step 4: Create the Page

1. In the Editor, click **Add** → **Page** → **Blank Page**
2. Name it "API Key" or similar
3. Set the URL slug (Page Settings → SEO), e.g. **`api-key`** → `https://www.yoursite.net/api-key`, or **`account/api-key`** for a nested path. The mobile app’s `WIX_ACCOUNT_URL` must match **exactly**.
4. Set permissions to **Members Only** (Page Settings → Permissions)

### Step 5: Add Page Elements

Add these elements with the specified IDs (click element → Properties panel → ID):

| Element | ID | Suggested Style |
|---------|-----|-----------------|
| Text | `statusText` | Large, centered |
| Text | `apiKeyText` | Monospace font, hidden initially |
| Button | `copyButton` | "Copy API Key", hidden initially |
| Link | `completeSignInLink` | Optional; shown if automatic redirect fails (e.g. "Click here if app didn't sign in") |

### Step 6: Add Page Code

1. Click on the page (not an element)
2. In the code panel at the bottom, paste the contents of `api-key-page.js`

### Step 7: Publish

Click **Publish** to make the page live.

## How It Works

### Normal Flow (Visiting the page directly)

1. User visits `https://www.livetranslate.net/api-key` (or your published slug)
2. Wix checks if user is logged in (redirects to login if not)
3. Page fetches API key from backend
4. API key is displayed with a copy button

### SSO Flow (From Desktop App)

1. User clicks "Sign in with Wix" in the desktop app
2. Browser opens: `https://www.livetranslate.net/app-auth?redirect_uri=http://localhost:12345/` (public page)
3. **app-auth** page immediately redirects to `/api-key?redirect_uri=...` (preserves param)
4. If not logged in: Wix redirects to login; after sign-in, user returns to `/api-key?redirect_uri=...`
5. **api-key** page fetches API key from backend
6. Page redirects to: `http://localhost:12345/?api_key=xxx`
7. Desktop app's callback server receives the API key → user is signed in
8. Browser tab can be closed

**To improve reliability** (redirect_uri preserved through login): create the `/app-auth` page and set `LIVE_TRANSLATE_WIX_APP_AUTH_PATH=/app-auth`. Otherwise the app opens `/api-key` directly.

### app-auth Page Setup (recommended)

1. Create a new page with slug **`app-auth`**
2. Do **NOT** set it to Members Only (keep it public)
3. Paste `app-auth-page.js` into the page code panel
4. Publish

### Login Page Setup (optional, for /login?returnUrl)

For "Sign in with Wix" to open livetranslate.net/login first, add `login-page.js` to your login page:

1. Open your Wix site in the Editor
2. Go to your **Login** page (slug `/login`)
3. Click the page (not an element) → code panel at bottom
4. Paste the contents of `login-page.js`
5. Publish

This registers `authentication.onLogin` to redirect to the `returnUrl` query param after sign-in. Alternatively, paste the code into `masterPage.js` for site-wide behavior.

## Security

### Redirect URI Validation

The `redirect_uri` is validated against a whitelist to prevent malicious redirects:

- `localhost` / `127.0.0.1` (http only) - for desktop app
- `livetranslate.app` and subdomains (https only)
- `livetranslate.net` and subdomains (https only)

### Secret Management

The `WIX_SYNC_SECRET` is stored in Wix Secrets Manager and only accessed from the backend web module. It's never exposed to the frontend.

### Member-Only Access

The page requires Wix membership authentication. Only logged-in members can access their API keys.

## Troubleshooting

### "Please log in to view your API key"

The user isn't logged in. They'll be redirected to login automatically if page permissions are set to "Members Only".

### "Could not retrieve API key"

Check:
1. `WIX_SYNC_SECRET` is set in Secrets Manager
2. The secret value matches your backend's `WIX_SYNC_SECRET`
3. Backend server is running and accessible

### SSO not completing (app keeps waiting)

1. Ensure `api-key-page.js` is deployed on your Wix api-key page with the `redirect_uri` logic
2. The api-key page must call `syncMemberToBackend` then `getApiKeyForMember`
3. `api-key.web.js` `BACKEND_URL` must match your deployed backend (api.livetranslate.app or your URL)
4. `WIX_SYNC_SECRET` in Wix Secrets Manager must match backend's `WIX_SYNC_SECRET`
5. **Fallback:** If redirect fails, copy your API key from the website and paste it in the app

### Desktop app times out

Check:
1. The published page URL matches what you pass as `WIX_ACCOUNT_URL` in the app (e.g. `/api-key`)
2. The page code is exactly from `api-key-page.js` (must check for `redirect_uri` and redirect to localhost)
3. The backend module is in `backend/api-key.web.js` and `BACKEND_URL` matches your deployed backend
4. `WIX_SYNC_SECRET` in Wix Secrets Manager matches your backend's `WIX_SYNC_SECRET`
5. **Tip:** Log into livetranslate.net in your browser first, then click "Sign in with Wix" — avoids query param loss during login redirect
