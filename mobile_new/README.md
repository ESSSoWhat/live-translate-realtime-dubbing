# Live Translate Mobile

Flutter app for real-time mic translation via the Live Translate backend. Supports **Android**, **iOS**, **Web**, **Windows**, **macOS**, and **Linux**.

## Prerequisites

- Flutter SDK (stable channel)
- **Android**: Android Studio / SDK, JDK 17+
- **iOS/macOS**: Xcode (macOS only)
- **Windows**: Visual Studio 2022 with "Desktop development with C++"
- **Linux**: clang, cmake, ninja, pkg-config, libgtk-3-dev

## Run (development)

```bash
cd mobile_new
flutter pub get
flutter run
```

Pick a device (Chrome, Windows, Android, etc.) when prompted.

## Build for each platform

### Android (APK / App Bundle)

```bash
flutter build apk          # debug APK
flutter build apk --release # release APK
flutter build appbundle     # AAB for Play Store
```

Output: `build/app/outputs/flutter-apk/` or `build/app/outputs/bundle/`.

### iOS (macOS only)

```bash
flutter build ios --release
# Open ios/Runner.xcworkspace in Xcode to archive and distribute.
```

### Web

```bash
flutter build web
```

Output: `build/web/`. Serve with any static host (e.g. `flutter run -d chrome` or deploy `build/web/` to Firebase Hosting, Netlify, etc.).

### Windows

```bash
flutter build windows
```

Output: `build/windows/x64/runner/Release/` (includes `.exe` and DLLs). Zip this folder for distribution.

### macOS (macOS only)

```bash
flutter build macos
```

Output: `build/macos/Build/Products/Release/`. Optionally create a `.app` bundle or DMG for distribution.

### Linux

```bash
flutter build linux
```

Output: `build/linux/x64/release/bundle/`. Distribute the bundle folder or package it (e.g. AppImage, snap).

## Optional: API and Qonversion

- **API base URL**: pass at build/run time, e.g.  
  `flutter run --dart-define=API_BASE_URL=https://api.example.com/`
- **Qonversion** (IAP): only used on **Android** and **iOS**. Web and desktop builds use a stub (no in-app purchases). Set `QONVERSION_PROJECT_KEY` via `--dart-define=QONVERSION_PROJECT_KEY=your_key` for mobile.

## Troubleshooting

- **Gradle**: Android requires Gradle 9.1+ (see `android/gradle/wrapper/gradle-wrapper.properties`).
- **Native build failures**: Ensure Flutter SDK path has no spaces (e.g. avoid `C:\Live Translate\flutter`). Use a path like `C:\flutter` or `S:\flutter`.
- **Web**: If you see Wasm/native-asset errors, try `flutter build web --no-wasm-dry-run` or use the standard (non-Wasm) web build.
