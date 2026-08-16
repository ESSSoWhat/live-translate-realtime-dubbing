# Overlay live translation (Android)

When you start translation from Home, the app can show a **draggable bubble** over other apps with status, live captions (source + translation), mute TTS, and stop.

## Grant “Display over other apps”

1. Start translation once; Android may open the special app-access settings page.
2. Or open **Settings → Apps → Live Translate → Display over other apps** (wording varies by OEM) and allow it.
3. If you deny the permission, **in-app mic translation still works**; Home shows a short snackbar that the overlay is unavailable.

## Microphone-only audio

Overlay mode uses the **device microphone** only (same chunked pipeline as in-app translate). It can pick up speech near the phone (including media playing loudly through the speaker), but it does **not** capture YouTube/Telegram/etc. **playback audio** internally. Capturing other apps’ audio needs MediaProjection and is out of scope for this feature.

## Background notification

While translation runs with the overlay / background keep-alive:

- A **foreground-service notification** stays visible (required by Android for mic + overlay).
- Do not swipe it away if you want capture to keep running when the app is not in the foreground.
- Stop from the bubble or from Home to tear down the overlay, notification, and mic loop.

## Quick test checklist

- Start translate on Home → grant overlay → leave app → open another app → bubble visible; captions update when speaking into the mic; TTS plays unless muted.
- Stop from the bubble and from Home; both tear down cleanly.
- Deny overlay permission → in-app translate still works.
- Background ~1+ minute: notification present; recording does not die silently.
- Premium/paywall still blocks start for free users.
