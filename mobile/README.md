# Live Translate Mobile (Flutter)

Android app for real-time mic translation using the Live Translate backend. Translated audio plays through the device speaker.

## Setup

1. Install [Flutter](https://docs.flutter.dev/get-started/install).
2. From this directory run:
   ```bash
   flutter pub get
   flutter create . --project-name live_translate_mobile   # if platform folders are missing
   ```
3. Configure API base URL (optional):  
   Pass `--dart-define=API_BASE_URL=https://your-api.com/` when running or building (e.g. `flutter run --dart-define=API_BASE_URL=https://your-api.com/`). If not set, the app uses the default backend URL. Values passed with `--dart-define` apply only to that invocation; for release builds you must pass the same flag (e.g. `flutter build apk --dart-define=API_BASE_URL=https://your-api.com/`). The app can run without the variable using the default backend; configure backend auth/API keys in the app as required (see `lib/config/api_config.dart`).

## Run

```bash
flutter run
```

## Build APK

To include the API base URL in the release bundle, pass the same `--dart-define` you use in development:

```bash
flutter build apk --release --dart-define=API_BASE_URL=https://your-api.com/
```

Without `--dart-define=API_BASE_URL=...`, the release build uses the default backend URL.

## Virtual mic on Android

The Android app is standalone: it uses the Live Translate backend for translation but does not require the Windows desktop app. Translated audio is played to the device's speaker/earpiece. Other apps on the same device cannot use this as a "microphone" input—that is an Android/platform limitation; there is no in-app workaround for routing translated audio as a system mic on device. For virtual mic routing into Discord, Zoom, etc., use the **Windows desktop app** with **VB-Cable** (a virtual audio cable): set the desktop app's output to CABLE Input and set Discord/Zoom input to CABLE Output. See [VB-Audio Cable](https://vb-audio.com/Cable/) for setup.
