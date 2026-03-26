# Live Translate — Deployment

Deploy backend, website (Wix), mobile (Android/iOS), and desktop (Windows). All deployments are gated by CI tests. **Auth, subscriptions, and API keys are driven by Wix.**

## Wix (website, sign-in, subscriptions, API keys)

- **Website**: [Wix](https://www.wix.com) — host **www.livetranslate.net** on Wix (custom domain in Wix → Domains). Marketing pages, pricing, and account/settings live here.
- **Sign-in**: Wix **Members** — users sign up and log in on the Wix site (email/password or social login configured in Wix).
- **Subscriptions**: Wix **Pricing Plans** — plans (Free trial, Hobby, Pro, Early Adopters) and billing are managed in Wix. Backend tier is synced via **POST /api/v1/billing/wix/sync** (see [backend/WIX_SYNC.md](backend/WIX_SYNC.md)).
- **API keys**: Backend issues per-user API keys. From Wix (Velo), call **POST /api/v1/auth/api-key** with the member’s email (authenticated with `WIX_SYNC_SECRET`); show the returned API key on a members-only account page so users can paste it into the desktop or mobile app.
- **Configure**: [Wix Dashboard](https://manage.wix.com) → your site → Settings, Members, Pricing Plans, Velo (Code), Secrets Manager for `WIX_SYNC_SECRET` and backend URL.

## How and where to access your apps

| App | Run locally | Access when deployed |
| ----- | ----- | ----- |
| **Website** | Wix Editor / Preview | <https://www.livetranslate.net> (Wix; custom domain) |
| **Backend API** | `cd backend` → `python -m uvicorn app.main:app --reload` → <http://localhost:8000> | Railway (or Docker) URL (e.g. `https://your-app.railway.app`) — set `WIX_SYNC_SECRET`, `SUPABASE_DB_URL`, `ELEVENLABS_API_KEY`, etc. |
| **Mobile (Android)** | `cd mobile` → `flutter run` | **Play Store**: [Google Play Console](https://play.google.com/console). Users sign in via **API key** from Wix account page. |
| **Mobile (iOS)** | `cd mobile` → `flutter run` or Xcode | **App Store**: [App Store Connect](https://appstoreconnect.apple.com). Users sign in via **API key** from Wix account page. |
| **Desktop (Windows)** | `cd live-dubbing` → `python -m live_dubbing` | **Installer**: [GitHub Releases](https://github.com/YOUR_ORG/YOUR_REPO/releases). Users enter **API key** from Wix account page. |

**Manage / configure**
- **Wix**: Wix Dashboard → site settings, Members, Pricing Plans, Velo, Secrets, custom domain.
- **Backend**: [Railway Dashboard](https://railway.app/dashboard) (Variables, Deployments).
- **Android / iOS**: Play Console, App Store Connect (releases, signing).
- **Releases (APK, AAB, Windows zip)**: GitHub repo → **Releases**.

---

## Testing (gate for deployment)

- **CI (on every push/PR)**  
  - **Backend**: `pytest tests/` with stub env vars.  
  - **Website**: `npm run lint`, `npm run build`.  
  - **Flutter**: `flutter pub get`, `flutter analyze`, `flutter test`, `flutter build apk --debug`.  
  - **Desktop (optional)**: Windows runner can run `pytest tests/` in `live-dubbing/` (e.g. `tests/test_vad.py`).

- **Manual**  
  - Desktop: GUI/smoke test (start app, select VB-Cable, run mic translate).  
  - Store: Acceptance testing after uploading to Play Console / App Store Connect.

---

## 1. Backend

- **Railway (recommended)**  
  - Connect repo, set root directory to `backend/`.  
  - Use existing Dockerfile. Set env vars: `SUPABASE_DB_URL`, `ELEVENLABS_API_KEY`, `WIX_SYNC_SECRET`; optionally `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` if still using Supabase for DB; `STRIPE_*` only if you use Stripe.  
  - Deploy from `main` or a tag.

- **Generic Docker**  
  - From repo root: `docker build -f backend/Dockerfile backend`.  
  - Run with same env vars, port 8000.

---

## 2. Website — Wix (www.livetranslate.net)

- **Wix**  
  - Create or use an existing Wix site. Enable **Members** and **Pricing Plans**.  
  - In Wix → **Domains**: connect **www.livetranslate.net** (and optionally **livetranslate.net** with redirect to www).  
  - In **Velo**: add code to call backend **POST /billing/wix/sync** and **POST /auth/api-key** (see [backend/WIX_SYNC.md](backend/WIX_SYNC.md)). Store backend URL and `WIX_SYNC_SECRET` in Wix Secrets Manager.  
  - Members-only account page: after sync, call API-key endpoint and show the key once so users can copy it into the desktop/mobile app.

- **Wix CLI apps (`wix-app/`)**  
  - Dashboard / embedded apps built with `@wix/cli` (e.g. `wix-app/live-translate-jsw`, `wix-app/blank-canvas`).  
  - **Login once:** `npx wix login` from the app folder (or repo root with `npx wix`).  
  - **Release a new version:** `cd wix-app/<app-name>` → `npm run release` (runs `wix app release`). Use `-t minor|major` and `-c "comment"` if needed (`npx wix app release --help`).  
  - **Interactive terminal:** The release flow opens prompts after upload/preview; run it in a normal TTY (Windows Terminal, PowerShell, or VS Code/Cursor integrated terminal). Agent or piped non-interactive shells may fail with an Ink/raw-mode error.

- **Optional: Next.js (website/)**  
  - If you keep the repo’s Next.js site for a separate dashboard or marketing, deploy to Vercel/Netlify with root `website/`. For a Wix-only setup, the live site is Wix; Next.js can be used for internal or redirect pages only.

---

## 3. Mobile — CI release builds

- **Local development (Android / Gradle)**  
  - Android builds require **JDK 17** (Gradle 8.x does not support Java 25+).  
  - Set `JAVA_HOME` to a JDK 17 install, or in Cursor: **Settings** → search “java configuration runtimes” → add JDK 17; set **Gradle: Java Home** to that JDK.  
  - After changing JDK: open a new terminal; run **Java: Clean Java Language Server Workspace** → Restart, then **Gradle: Refresh Gradle Project**.

- **App auth (Wix + API key)**  
  - Users sign in on **Wix** (Members). On the Wix account page, Velo calls the backend to sync tier and to create/return an **API key**.  
  - Desktop and mobile apps **do not** use Google/Apple sign-in to the backend; they ask the user to paste the **API key** from the Wix account page (or scan a QR / deep link). The app sends `Authorization: Bearer <api_key>` to the backend.  
  - Ensure backend env includes `WIX_SYNC_SECRET` and that Wix Velo calls **POST /billing/wix/sync** and **POST /auth/api-key** as in [backend/WIX_SYNC.md](backend/WIX_SYNC.md).

- **Android (on tag `v*`)**  
  - Workflow: `release-android` runs `flutter test`, then (when secrets set) decodes `ANDROID_KEYSTORE_BASE64` to `mobile/android/upload-keystore.jks` and writes `key.properties`.  
  - Builds `flutter build apk --release` and `flutter build appbundle --release`, uploads APK and AAB to GitHub Release.

- **iOS (on tag `v*`)**  
  - Workflow: `release-ios` runs `flutter test`, then `flutter build ios --release --no-codesign`.  
  - For IPA and App Store: configure code signing in CI (certificate + provisioning profile) and use `flutter build ipa`; add an upload step for the IPA. See store section below.

### Secrets (CI)

| Secret | Used by | Purpose |
| ------ | ------- | ------- |
| `ANDROID_KEYSTORE_BASE64` | release-android | Base64-encoded release keystore (.jks) |
| `ANDROID_KEYSTORE_PASSWORD` | release-android | Keystore password |
| `ANDROID_KEY_PASSWORD` | release-android | Key password |
| `ANDROID_KEY_ALIAS` | release-android | Key alias |
| (iOS) Certificate + provisioning profile | release-ios | For `flutter build ipa` and upload |

**Expo (if you use Expo/React Native)**  
Point the app at your backend URL and use the same API-key flow (user pastes key from Wix account page).

---

## 4. Mobile — Play Store / App Store

- **Play Store**  
  - Build AAB in CI (or locally) with release signing (secrets above).  
  - In Google Play Console: create app, store listing, content rating, privacy policy, target countries.  
  - Upload AAB via Console or Google Play Developer API / fastlane `supply` (service account + API access).  

- **App Store**  
  - Build IPA with distribution cert and App Store provisioning profile (in CI or Xcode).  
  - In App Store Connect: create app, metadata, screenshots, submit for review.  
  - Upload via Xcode Organizer, Transporter, or fastlane `deliver` (App Store Connect API key or Apple ID + app-specific password).

---

## 5. Desktop (Windows installer)

- **Build (manual or CI)**  
  - On Windows: install FFmpeg (e.g. add to PATH or set `FFMPEG_DIR` to the `bin` folder containing `ffmpeg.exe` and `ffprobe.exe`).  
  - In `live-dubbing/`: create venv, `pip install -e ".[dev]"`, install PyTorch CPU and PyInstaller, then run `pyinstaller live_translate.spec`.  
  - Optional: run Inno Setup on `installer.iss` to produce the installer in `dist/`.  
  - **Portable spec**: `live_translate.spec` uses `FFMPEG_DIR` (defaults to `S:\Coding project\ffmpeg\bin` on your machine). In CI, set `FFMPEG_DIR` to the runner’s FFmpeg path.

- **GitHub Release**  
  - Attach Windows installer (or zip of `dist/LiveTranslate/`) to a release: `gh release upload <tag> <file>` or use the Releases UI.

---

## 6. Optional CI

- **Windows desktop job (on tag)**  
  - Implemented in `.github/workflows/ci.yml`: `release-desktop` runs on tag `v*` on `windows-latest`, installs FFmpeg (Chocolatey), sets `FFMPEG_DIR`, builds with PyInstaller, zips `dist/LiveTranslate`, and uploads to GitHub Release.  
  - For a signed installer (Inno Setup), run Inno Setup locally or add a step to install it in CI and run `installer.iss`.

- **Store upload automation**  
  - Play: fastlane `supply` or Play Developer API.  
  - App Store: fastlane `deliver` or App Store Connect API.
