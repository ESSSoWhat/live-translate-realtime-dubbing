# MediaProjection phase 2 (app / system audio)

Desktop Live Translate can capture **other apps’ playback** (WASAPI / process loopback). Android has no equivalent without user consent.

## Goal

Approximate desktop “App Audio” mode:

1. User starts translation and chooses **Capture media audio**.
2. Android shows the system **MediaProjection** / screen-capture consent dialog.
3. App uses `AudioPlaybackCaptureConfiguration` (API 29+) to capture mixed playback PCM.
4. Feed PCM into the existing STT → translate → TTS pipeline (same as mic chunks).

## Out of scope (permanent)

- **Play TTS as another app’s microphone** (VB-Cable / virtual mic) — not available on Android.
- True per-app isolation like Windows process loopback — MediaProjection captures mixed playback (with optional UID allow/deny lists where OEM supports it).

## Implementation sketch (future PR)

- Native `MediaProjection` permission Activity + foreground service type `mediaProjection`.
- Dart `MethodChannel` to start/stop capture and stream PCM frames.
- Reuse `MicTranslateService` ingest path (or shared chunk processor) with WAV framing at 16 kHz mono.
- UI toggle on Home: Mic vs Media audio (mutually exclusive).
- Document Play Store policy: declare `FOREGROUND_SERVICE_MEDIA_PROJECTION` and justify screen capture.

## Status

Not implemented in the mic-mode parity release. Track as a separate PR after Home captions / voices / settings land.
