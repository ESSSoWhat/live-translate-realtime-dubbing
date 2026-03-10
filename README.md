# Live Translate

Real-time audio translation and voice-cloned dubbing. Capture audio, translate with [ElevenLabs](https://elevenlabs.io/) APIs, and output dubbed audio using a cloned voice.

**Website:** [www.livetranslate.net](https://www.livetranslate.net)

## Download

- **Windows:** [Download for Windows](https://www.livetranslate.net/download/win) (always latest)
- **Android:** [Download APK](https://www.livetranslate.net/download/android) (always latest)
- **All versions:** [Releases](https://github.com/ESSSoWhat/live-translate-realtime-dubbing/releases)

## Repository structure

| Directory    | Description |
|-------------|-------------|
| `live-dubbing/` | Windows desktop app (PyQt6, Python). App dubbing + mic translate. |
| `backend/`      | FastAPI server: auth (Supabase), API proxy (ElevenLabs), billing (Stripe), usage. |
| `mobile/`       | Flutter app (Android/iOS). Mic translation, auth, paywall. |
| `website/`      | Next.js site (www.livetranslate.net). Marketing, download, login, dashboard. |

## Quick start

### Windows desktop

```bash
cd live-dubbing
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
python -m live_dubbing
```

Requires [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) and an [ElevenLabs API key](https://elevenlabs.io/). See [live-dubbing/README.md](live-dubbing/README.md).

### Backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # Configure Supabase, ElevenLabs, etc.
python -m uvicorn app.main:app --reload
```

Runs at `http://localhost:8000`. Set `LIVE_TRANSLATE_BACKEND_URL=http://localhost:8000` to point the desktop app to it.

### Website

```bash
cd website
npm install
npm run dev
```

Runs at `http://localhost:3000`.

### Mobile

```bash
cd mobile
flutter pub get
flutter run
```

## Supported languages

English, Japanese, Korean, Chinese (Mandarin), Indonesian, Thai, Russian, Hindi, Vietnamese, Filipino (Tagalog).

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

- [ElevenLabs](https://elevenlabs.io/) – Voice AI, STT, TTS, voice cloning  
- [Silero VAD](https://github.com/snakers4/silero-vad) – Voice activity detection  
- [VB-Audio](https://vb-audio.com/Cable/) – Virtual audio cable (Windows)  
- [Supabase](https://supabase.com/) – Auth and database  
