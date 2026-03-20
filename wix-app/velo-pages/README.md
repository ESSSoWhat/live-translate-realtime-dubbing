# Wix Velo Page: /account/api-key

This folder contains the Velo code for the API key page that enables SSO with the Live Translate desktop app.

## Files

| File | Purpose | Where to add |
|------|---------|--------------|
| `api-key-page.js` | Frontend page code | Page code panel |
| `api-key.web.js` | Backend web module | `backend/` folder |

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
3. Set the URL to `/account/api-key` (Page Settings → SEO → URL slug)
4. Set permissions to **Members Only** (Page Settings → Permissions)

### Step 5: Add Page Elements

Add these elements with the specified IDs (click element → Properties panel → ID):

| Element | ID | Suggested Style |
|---------|-----|-----------------|
| Text | `statusText` | Large, centered |
| Text | `apiKeyText` | Monospace font, hidden initially |
| Button | `copyButton` | "Copy API Key", hidden initially |

### Step 6: Add Page Code

1. Click on the page (not an element)
2. In the code panel at the bottom, paste the contents of `api-key-page.js`

### Step 7: Publish

Click **Publish** to make the page live.

## How It Works

### Normal Flow (Visiting the page directly)

1. User visits `https://www.livetranslate.net/account/api-key`
2. Wix checks if user is logged in (redirects to login if not)
3. Page fetches API key from backend
4. API key is displayed with a copy button

### SSO Flow (From Desktop App)

1. User clicks "Sign in with Wix" in the desktop app
2. Browser opens: `https://www.livetranslate.net/account/api-key?redirect_uri=http://localhost:12345/`
3. User logs in to Wix (if not already logged in)
4. Page fetches API key from backend
5. Page redirects to: `http://localhost:12345/?api_key=xxx`
6. Desktop app's callback server receives the API key
7. User is automatically signed in - browser tab can be closed

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

### Desktop app times out

Check:
1. The page URL is exactly `/account/api-key`
2. The page code is correctly added
3. The backend module is in `backend/api-key.web.js`
4. User is logged into Wix in their browser
