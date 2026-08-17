# MediaProjection — Live Translate

Live Translate on Android uses the system **MediaProjection** consent dialog. Under Live mode you choose:

| Input | Pipeline |
|-------|----------|
| **App audio** | `AudioPlaybackCapture` → STT → translate → TTS |
| **Screen text** | Screen frames (VirtualDisplay) → on-device ML Kit OCR → translate → TTS |

## User flow

1. Choose **Live Translate** on Home.
2. Select **App audio** or **Screen text**.
3. Tap **Start Live Translate**.
4. Allow the **screen/audio capture** permission when prompted.
5. Optionally enable **Display over other apps** for the caption bubble.
6. **App audio:** play media with speech. **Screen text:** open an app with visible text (subtitles, chat, games).

**Mic Translate** still uses the device microphone only (no MediaProjection).

## Limits

- Requires **Android 10+**.
- App audio is mixed playback, not true per-app isolation like Windows process loopback.
- Some apps set `ALLOW_CAPTURE_BY_NONE` and **cannot** be captured for audio.
- Screen OCR uses a scaled frame (~720px long edge) about every 1.5s; heavy on CPU/battery vs audio mode.
- OCR script follows the Home **From** language (latin / CJK / Devanagari); `auto` uses latin.
- TTS uses accessibility audio usage so it is less likely to be re-captured into the audio pipeline.
- Virtual mic / “play as Zoom mic” remains unavailable on Android.

## Implementation

- Native: `PlaybackCaptureService` (`EXTRA_MODE` = `audio` \| `screen`) + MainActivity channels.
- Dart: `playback_capture.dart` (`PlaybackCaptureMode`) → `MicTranslateService` (`CaptureSource.playback` \| `screen`).
- OCR: `screen_ocr.dart` + `google_mlkit_text_recognition`.
- Overlay: same bubble when Live mode is on.
