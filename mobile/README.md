# Live Translate Mobile (Flutter)

Android app for real-time mic translation using the Live Translate backend. Translated audio plays through the device speaker.

## Setup

1. Install [Flutter](https://docs.flutter.dev/get-started/install).
2. From this directory run:
   ```bash
   flutter pub get
   flutter create . --project-name live_translate_mobile   # if platform folders are missing
   ```
3. **API base URL** (if you omit `--dart-define=API_BASE_URL=...`):
   - **Windows / macOS / Linux / iOS simulator:** `http://127.0.0.1:8000`
   - **Android emulator:** `http://10.0.2.2:8000` (reaches your PC’s localhost)
   - **Physical phone or production API:** you must pass `--dart-define=API_BASE_URL=http://YOUR_LAN_IP:8000/` or your deployed `https://...` URL.  
   Start the backend: `cd ../backend` → `python -m uvicorn app.main:app --reload`.

## Run

```bash
flutter run
```

## Build APK

To include the API base URL in the release bundle, pass the same `--dart-define` you use in development:

```bash
flutter build apk --release --dart-define=API_BASE_URL=https://your-api.com/
```

Without `--dart-define=API_BASE_URL=...`, the app targets **localhost** (see above). For a **release APK/IPA** pointing at a hosted API, always pass `--dart-define=API_BASE_URL=https://your-api/`.

## Virtual mic on Android

The Android app is standalone: it uses the Live Translate backend for translation but does not require the Windows desktop app. Translated audio is played to the device's speaker/earpiece. Other apps on the same device cannot use this as a "microphone" input—that is an Android/platform limitation; there is no in-app workaround for routing translated audio as a system mic on device. For virtual mic routing into Discord, Zoom, etc., use the **Windows desktop app** with **VB-Cable** (a virtual audio cable): set the desktop app's output to CABLE Input and set Discord/Zoom input to CABLE Output. See [VB-Audio Cable](https://vb-audio.com/Cable/) for setup.

## Google Sign-In (SSO)

For "Continue with Google" to work:

1. **Google Cloud Console** — Create (or use) an OAuth 2.0 **Web application** client. Note the **Client ID** (Web client ID).
2. **Supabase** — Auth → Providers → Google: enable and set Client ID + Client secret to that Web client.
3. **This app** — Set the same Web client ID:  
   - Build: `--dart-define=GOOGLE_WEB_CLIENT_ID=YOUR_WEB_CLIENT_ID.apps.googleusercontent.com`  
   - Or at runtime: `GOOGLE_WEB_CLIENT_ID=... flutter run`
4. **Android only** — Add your app’s SHA-1 and SHA-256 to the Google Cloud OAuth client:  
   `keytool -keystore path-to-keystore -list -v` → add the fingerprints in APIs & Services → Credentials.

## Troubleshooting

- **"Cannot reach API" / connection errors**  
  The app must reach your FastAPI backend. In debug, run the backend on port 8000. On a **physical phone**, `localhost` is the phone itself — use your PC’s LAN IP and `--dart-define=API_BASE_URL=http://192.168.x.x:8000/`.

- **Android: "this and base files have different roots" (e.g. `google_sign_in_android:compileDebugUnitTestSources`)**  
  This occurs when the project is on a different Windows drive than the Pub cache (e.g. project on `S:\` and Pub cache on `C:\`). **Fix:** use a Pub cache on the same drive as the project, then refresh dependencies:
  ```powershell
  # From mobile/ — use a local .pub-cache on the same drive as the project
  $env:PUB_CACHE = "$(Get-Location)\.pub-cache"
  flutter clean
  flutter pub get
  ```
  Then run or build Android as usual. You can set `PUB_CACHE` permanently in your user environment to the same drive as your projects if you often work from another drive.

- **Android: "Unsupported class file major version 69" or "Can't use Java 25 and Gradle"**  
  The Android build requires **JDK 17** (or 21). If you use Java 25, set `JAVA_HOME` to a JDK 17 install, or in your IDE set the Gradle JVM to JDK 17 (e.g. File → Settings → Build, Execution, Deployment → Build Tools → Gradle → Gradle JDK). The project’s `android/.java-version` file suggests 17 for tools that support it.

- **Dart: "Target of URI doesn't exist: package:flutter/material.dart"**  
  Usually the analyzer is running from the workspace root instead of this project. Open the **`mobile`** folder as the project root in your IDE, or run `flutter pub get` from this directory and ensure the Dart/Flutter extension is using the Flutter SDK and this project’s `.dart_tool`.
