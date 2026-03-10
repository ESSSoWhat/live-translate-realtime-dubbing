# Live Translate — Deployment

Deploy backend, website (www.livetranslate.net), mobile (Android/iOS with store submission), and desktop (Windows installer). All deployments are gated by CI tests.

## How and where to access your apps

| App | Run locally | Access when deployed |
| ----- | ----- | ----- |
| **Website** | `cd website` → `npm run dev` → open <http://localhost:3000> | <https://www.livetranslate.net> (after Vercel/Netlify deploy) |
| **Backend API** | `cd backend` → `python -m uvicorn app.main:app --reload` → <http://localhost:8000> | Your Railway (or Docker) URL (e.g. `https://your-app.railway.app`) |
| **Mobile (Android)** | `cd mobile` → `flutter run` (device/emulator) | **Play Store**: [Google Play Console](https://play.google.com/console) → your app → Release → Production. **Pre-release**: same place or install APK from GitHub Release. |
| **Mobile (iOS)** | `cd mobile` → `flutter run` (Mac + device/simulator) or open `ios/Runner.xcworkspace` in Xcode | **App Store**: [App Store Connect](https://appstoreconnect.apple.com) → My Apps → your app. **TestFlight**: same place. **Builds**: upload IPA from Xcode/Transporter or CI. |
| **Desktop (Windows)** | `cd live-dubbing` → `python -m live_dubbing` | **Installer**: [GitHub Releases](https://github.com/YOUR_ORG/YOUR_REPO/releases) → pick tag (e.g. `v0.1.0`) → download `LiveTranslate-<tag>.zip` or the Windows installer if you added Inno Setup. |

**Manage / configure**
- **Website**: [Vercel Dashboard](https://vercel.com/dashboard) or [Netlify](https://app.netlify.com) (project → Domains, Env Vars).
- **Backend**: [Railway Dashboard](https://railway.app/dashboard) (project → Variables, Deployments).
- **Android**: [Play Console](https://play.google.com/console) (releases, store listing, signing).
- **iOS**: [App Store Connect](https://appstoreconnect.apple.com) (TestFlight, App Store, signing in [Developer](https://developer.apple.com/account)).
- **Releases (APK, AAB, Windows zip)**: GitHub repo → **Releases** (right sidebar or `https://github.com/YOUR_ORG/YOUR_REPO/releases`).

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
  - Use existing Dockerfile; set env vars from `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `ELEVENLABS_API_KEY`, `STRIPE_SECRET_KEY` (if used).  
  - Deploy from `main` or from tag; optional: deploy job in CI via Railway CLI or GitHub integration.

- **Generic Docker**  
  - From repo root: `docker build -f backend/Dockerfile backend` (build context = backend).  
  - Run with same env vars, port 8000.

---

## 2. Website (www.livetranslate.net)

- **Vercel**  
  - Connect repo; set **root directory** to `website/`.  
  - In Project Settings → Domains, add **www.livetranslate.net** as the production domain. Optionally add **livetranslate.net** and set up a redirect to www (Vercel supports this in Domains).  
  - Set **NEXTAUTH_URL** to `https://www.livetranslate.net` in Environment Variables so NextAuth redirects and callbacks use the canonical URL.  
  - Build uses `npm run build` (Next.js); `website/vercel.json` sets framework.  
  - Set other env vars in Vercel (e.g. Google OAuth, API URL) as needed.  
  - Deploy on push to `main` via Vercel GitHub integration; production serves at <https://www.livetranslate.net>.

- **Netlify**  
  - Alternatively connect repo, base directory `website/`, build command `npm run build`, publish directory `.next` or use Next.js runtime; add custom domain <https://www.livetranslate.net>.

---

## 3. Mobile — CI release builds

- **Local development (Android / Gradle)**  
  - Android builds require **JDK 17** (Gradle 8.x does not support Java 25+).  
  - Set `JAVA_HOME` to a JDK 17 install, or in Cursor: **Settings** → search “java configuration runtimes” → add JDK 17; set **Gradle: Java Home** to that JDK.  
  - After changing JDK: open a new terminal; run **Java: Clean Java Language Server Workspace** → Restart, then **Gradle: Refresh Gradle Project**.

- **SSO (Google / Apple)**  
  - **Google on Android**: Supabase validates the ID token using your Google **web** client ID. Set `GOOGLE_WEB_CLIENT_ID` when building (e.g. `--dart-define=GOOGLE_WEB_CLIENT_ID=YOUR_WEB_CLIENT_ID.apps.googleusercontent.com`). Use the same web client ID as in Supabase → Auth → Providers → Google.  
  - **Google on Windows/macOS/Linux**: The app opens the system browser and uses the backend’s OAuth flow. In **Supabase** → **Auth** → **URL Configuration** → **Redirect URLs**, add `http://localhost` (and optionally `http://127.0.0.1`) so the callback after Google sign-in is allowed.  
  - **Apple**: Ensure Sign in with Apple is enabled in Supabase and in the Apple Developer app ID; iOS has Runner.entitlements, Android is supported by the package on API 13+.

### Google Sign-In setup (Web client + Android SHA)

Do this once so the mobile app can use “Continue with Google” and the backend (Supabase) can verify the token.

1. **Google Cloud Console**  
   - Go to [Google Cloud Console](https://console.cloud.google.com) → your project (or create one) → **APIs & Services** → **Credentials**.  
   - **Create OAuth 2.0 Client ID** (or use an existing one):  
     - Application type: **Web application**.  
     - Name it e.g. “Live Translate Web (Supabase)”.  
     - Under **Authorized redirect URIs** add your Supabase callback, e.g. `https://<PROJECT_REF>.supabase.co/auth/v1/callback` (find the exact URL in Supabase → Auth → URL Configuration).  
   - Copy the **Client ID** (ends with `.apps.googleusercontent.com`) and the **Client secret**.  
   - Optional for web: add your production site (e.g. `https://www.livetranslate.net`) to Authorized JavaScript origins if you use Google Sign-In on the website.

2. **Supabase**  
   - **Dashboard** → **Authentication** → **Providers** → **Google**.  
   - Enable Google, paste the **Client ID** and **Client secret** from step 1.  
   - Save. This is the “web” client Supabase uses to verify ID tokens from the app.

3. **Flutter app — web client ID**  
   - Build/run the app with that same **Client ID** as the web client:  
     `flutter run --dart-define=GOOGLE_WEB_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com`  
   - For release builds (e.g. CI or local):  
     `flutter build apk --dart-define=GOOGLE_WEB_CLIENT_ID=...`  
   - So: the **same** value as in Supabase → Google provider.

4. **Android — SHA-1 and SHA-256**  
   - Google requires your app’s package name and signing certificate fingerprint(s).  
   - **Debug** (local/dev):  
     ```bash
     cd mobile/android
     ./gradlew signingReport
     ```  
     Or: `keytool -keystore ~/.android/debug.keystore -list -v` (alias `androiddebugkey`, storepass `android` if prompted).  
   - **Release**: `keytool -keystore path-to-release-keystore.jks -list -v` (use your upload/ release keystore path and alias).  
   - In **Google Cloud Console** → **Credentials** → **Create credentials** → **OAuth client ID** → Application type: **Android**.  
   - Package name: `app.livetranslate.live_translate_mobile`.  
   - Paste the **SHA-1** from the signing report (and SHA-256 if the form allows). Create one Android client for debug (debug SHA-1) and one for release (release SHA-1), or add both fingerprints to one client if the UI allows.  
   - The app code uses the **Web** client ID as `GOOGLE_WEB_CLIENT_ID` (for Supabase); the Android client(s) just register the app with Google so sign-in is allowed.  
   - After adding the Android client(s), wait a few minutes and try “Continue with Google” again.

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
Set in `.env.local` or in EAS/Expo config: `EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_KEY` (Supabase URL and anon key). See `expo/.env.example`.

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
