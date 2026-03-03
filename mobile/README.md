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
   `flutter run --dart-define=API_BASE_URL=https://your-api.com/`

## Run

```bash
flutter run
```

## Build APK

```bash
flutter build apk --release
```

## Virtual mic on Android

Translated audio is played to the device's audio output (speaker/earpiece). Other apps on the same device cannot use this as a "microphone" input. For virtual mic routing to Discord/Zoom, use the Windows desktop app with VB-Cable.
