# Overlay live translation (Android)

When you start translation from Home, the app can show a **draggable bubble** over other apps with status, live captions (source + translation), **voice picker**, **TTS volume**, mute TTS, and stop. Caption font size and opacity follow **Settings**.

## Grant “Display over other apps”

1. Start translation once; Android may open the special app-access settings page.
2. Or open **Settings → Apps → Live Translate → Display over other apps** (wording varies by OEM) and allow it.
3. If you deny the permission, **in-app mic translation still works**; Home shows a short snackbar that the overlay is unavailable.

## Microphone-only audio (parity with desktop Mic mode)

Overlay / Home translation uses the **device microphone** (chunked WAV → backend STT/translate/TTS). This matches the desktop **Microphone / Mic Translate** product surface.

It **can** capture YouTube/Telegram **playback** or **on-screen text** when you use **Live Translate** (MediaProjection consent; choose App audio or Screen text). See [MEDIA_PROJECTION_PHASE2.md](MEDIA_PROJECTION_PHASE2.md).

**Mic Translate** uses the microphone only.

Live Translate recording avoids exclusive mic audio focus so media can keep playing. Spoken TTS may briefly duck other media. Some apps block playback capture.

Desktop-only (not on Android): WASAPI/app loopback, VB-Cable “play as mic” into Zoom/Discord.

## Background notification

While translation runs with the overlay / background keep-alive:

- A **foreground-service notification** stays visible (required by Android for mic + overlay).
- Do not swipe it away if you want capture to keep running when the app is not in the foreground.
- Stop from the bubble or from Home to tear down the overlay, notification, and mic loop.

## Quick test checklist

- Start translate on Home → grant overlay → leave app → open another app → bubble visible; captions update when speaking into the mic; TTS plays unless muted; volume slider and voice dropdown work from the expanded bubble.
- Stop from the bubble and from Home; both tear down cleanly.
- Deny overlay permission → in-app translate still works.
- Background ~1+ minute: notification present; recording does not die silently.
- Premium/paywall still blocks start for free users.
