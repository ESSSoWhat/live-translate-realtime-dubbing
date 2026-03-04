# Live Dubbing

Real-time audio translation and voice-cloned dubbing for Windows applications.

## Overview

Live Dubbing captures audio from a selected Windows application, translates it in real-time from any source language to a target language, and outputs dubbed audio using ElevenLabs voice cloning to preserve the original speaker's voice.

## Features

- **Per-App Audio Isolation**: Capture audio from a specific application only
- **Dynamic Voice Cloning**: Automatically clone the speaker's voice from captured audio
- **Real-Time Translation**: Low-latency translation pipeline (~1-2 seconds)
- **10 Supported Languages**: English, Japanese, Korean, Chinese, Indonesian, Thai, Russian, Hindi, Vietnamese, Filipino (Tagalog)
- **Auto Language Detection**: Automatically detect source language
- **Voice Activity Detection**: Smart speech segmentation using Silero VAD

## Requirements

### System Requirements

- Windows 10 or Windows 11
- Python 3.10 or higher
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) (free) - Required for audio isolation

### API Requirements

- [ElevenLabs API Key](https://elevenlabs.io/) - For speech-to-text, translation, text-to-speech, and voice cloning

## Installation

1. **Install VB-Audio Virtual Cable**
   - Download from: https://vb-audio.com/Cable/
   - Run the installer and restart your computer

2. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd live-dubbing
   ```

3. **Create a virtual environment**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

4. **Install dependencies**
   ```bash
   pip install -e .
   ```

   Or for development:
   ```bash
   pip install -e ".[dev]"
   ```

5. **Configure your ElevenLabs API key**

   The first time you run the app, you'll be prompted to enter your API key, or you can set it via environment variable:
   ```bash
   set ELEVENLABS_API_KEY=your_api_key_here
   ```

## Usage

1. **Start the application**
   ```bash
   live-dubbing
   ```

   Or run directly:
   ```bash
   python -m live_dubbing
   ```

2. **Configure Audio Routing**
   - Select the target application from the dropdown
   - When prompted, configure Windows to route the app's audio to "CABLE Input"
     - Right-click speaker icon → Sound settings → App volume → Change output to "CABLE Input"

3. **Start Translation**
   - Select your target language
   - Click "Start Translation"
   - The app will capture 30-60 seconds of speech to clone the voice
   - Once cloning is complete, translated dubbed audio will play automatically

## Architecture

```
Target App Audio → VB-Cable → Capture → VAD → STT → Translate → TTS → Output
       │                                                           │
       └── Original audio muted (doesn't reach user)               │
                                                                   ↓
                                               User hears dubbed output only
```

### Processing Pipeline

1. **Audio Capture**: WASAPI loopback from VB-Cable output
2. **VAD (Voice Activity Detection)**: Silero VAD filters speech from silence
3. **Voice Cloning**: First 30-60s of speech used to clone voice via ElevenLabs IVC
4. **STT (Speech-to-Text)**: ElevenLabs Scribe v2 (150ms latency)
5. **Translation**: ElevenLabs built-in translation
6. **TTS (Text-to-Speech)**: ElevenLabs Flash v2.5 with cloned voice (75ms latency)

## Supported Languages

| Language | Code |
|----------|------|
| English | en |
| Japanese | ja |
| Korean | ko |
| Chinese (Mandarin) | zh |
| Indonesian | id |
| Thai | th |
| Russian | ru |
| Hindi | hi |
| Vietnamese | vi |
| Filipino (Tagalog) | tl |

## Configuration

Settings are stored in `%APPDATA%/LiveDubbing/settings.json`

### Audio Settings

- `sample_rate`: Audio sample rate (default: 16000)
- `chunk_size_ms`: Audio chunk size in milliseconds (default: 100)
- `buffer_size_ms`: Buffer size (default: 500)

### Voice Clone Settings

- `dynamic_capture_duration_sec`: Duration to capture for voice cloning (default: 30)
- `voice_stability`: Voice stability setting (0-1, default: 0.5)
- `voice_similarity`: Voice similarity setting (0-1, default: 0.75)

## Troubleshooting

### VB-Cable not detected

1. Ensure VB-Cable is properly installed
2. Restart your computer after installation
3. Check Windows Sound settings to verify "CABLE Input" and "CABLE Output" devices exist

### No audio captured

1. Verify the target app's audio output is set to "CABLE Input" in Windows sound settings
2. Ensure the app is actually producing audio
3. Check the audio level meter in the app

### High latency

- Close other resource-intensive applications
- Ensure stable internet connection for ElevenLabs API
- Try increasing `chunk_size_ms` in settings

### Voice clone quality issues

- Ensure at least 30 seconds of clear speech is captured
- Avoid background noise during the initial capture phase
- Consider using a higher quality audio source

## Development

### Running Tests

```bash
pytest tests/
```

### Release smoke test

Before releasing a build, verify on a Windows machine:

1. **Build with CPU-only PyTorch** (avoids `c10_cuda.dll` load errors on machines without CUDA):
   ```bash
   pip uninstall -y torch torchaudio
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
   pyinstaller spec.spec
   ```
   Then build the installer. You can use Inno Setup's `ISCC.exe`—either add it to your PATH or set `INNO_SETUP_PATH` (e.g. `set INNO_SETUP_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe` on Windows). Invoke it with `"%INNO_SETUP_PATH%" installer.iss` (or run `ISCC.exe installer.iss` if it's on PATH). On 64-bit Windows, Inno may be under `C:\Program Files\Inno Setup 6`; adjust the path for your version.
2. Start the app (`live-dubbing` or run the built installer).
3. Ensure VB-Cable is installed (Status bar shows "VB-Cable: OK").
4. **App dubbing**: Select an app from the dropdown, set its Windows output to "CABLE Input", start translation; confirm dubbed audio plays on the selected output device.
5. **Mic translate**: Open Tools → Mic Translate; select microphone, target language, and **Output device = CABLE Input**; start; speak into the mic; confirm translated audio is heard in another app (e.g. set Discord/Zoom input to "CABLE Output") and optionally on **Monitor output**. ("Monitor output" is the system playback or virtual output used to hear mixed audio—e.g. VB-Cable's virtual input/output or the OS "Stereo Mix"/monitor of the chosen device. See [VB-Cable setup](https://vb-audio.com/Cable/) for which device to select and how it fits into the live-dubbing setup.)

### Code Style

```bash
# Format code
black src/ tests/

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

## License

MIT License - See LICENSE file for details.

## Acknowledgments

- [ElevenLabs](https://elevenlabs.io/) - Voice AI platform
- [Silero VAD](https://github.com/snakers4/silero-vad) - Voice activity detection
- [VB-Audio](https://vb-audio.com/) - Virtual audio cable
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) - GUI framework
