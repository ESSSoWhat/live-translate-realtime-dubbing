"""Application settings and configuration management."""

import contextlib
import json
import os
from enum import Enum
from pathlib import Path

import structlog
from platformdirs import user_config_dir
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


def _load_env_file() -> None:
    """Load OPENAI_API_KEY and ELEVENLABS_API_KEY from .env if not set.

    Searches (in order): next to the running executable (for installed apps),
    current working directory, package root, and one level up.
    """
    import sys

    wanted = {"OPENAI_API_KEY", "ELEVENLABS_API_KEY"}
    missing = [k for k in wanted if not os.environ.get(k)]
    if not missing:
        return

    # Directories to search for .env
    pkg_root = Path(__file__).resolve().parents[2]
    search_dirs = [
        Path(sys.executable).resolve().parent,  # next to exe (installed app)
        Path.cwd(),
        pkg_root,
        pkg_root.parent,
    ]

    for directory in search_dirs:
        env_path = directory / ".env"
        if not env_path.is_file():
            continue
        with contextlib.suppress(OSError), open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"").strip()
                    if key in wanted and value:
                        os.environ[key] = value
                        missing = [k for k in missing if k != key]
                        if not missing:
                            return


class SupportedLanguage(Enum):
    """Supported languages for translation."""

    ENGLISH = "en"
    SPANISH = "es"
    JAPANESE = "ja"
    KOREAN = "ko"
    CHINESE = "zh"
    INDONESIAN = "id"
    THAI = "th"
    RUSSIAN = "ru"
    HINDI = "hi"
    VIETNAMESE = "vi"
    FILIPINO = "tl"

    @classmethod
    def get_display_name(cls, code: str) -> str:
        """Get display name for a language code."""
        names = {
            "en": "English",
            "es": "Spanish",
            "ja": "Japanese",
            "ko": "Korean",
            "zh": "Chinese (Mandarin)",
            "id": "Indonesian",
            "th": "Thai",
            "ru": "Russian",
            "hi": "Hindi",
            "vi": "Vietnamese",
            "tl": "Filipino (Tagalog)",
            "auto": "Auto-detect",
        }
        return names.get(code, code)

    @classmethod
    def get_all_languages(cls) -> list[tuple[str, str]]:
        """Return all languages as (code, name) tuples."""
        return [
            (lang.value, cls.get_display_name(lang.value)) for lang in cls
        ]

    @classmethod
    def get_source_languages(cls) -> list[tuple[str, str]]:
        """Get source languages including auto-detect."""
        return [("auto", "Auto-detect")] + cls.get_all_languages()


class AudioConfig(BaseModel):
    """Audio capture and playback configuration."""

    input_device_id: str | None = None
    output_device_id: str | None = None
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    chunk_size_ms: int = Field(default=100, ge=50, le=500)
    buffer_size_ms: int = Field(default=500, ge=100, le=2000)


class VoiceCloneConfig(BaseModel):
    """Voice cloning configuration."""

    dynamic_capture_duration_sec: float = Field(default=5.0, ge=3.0, le=120.0)
    voice_stability: float = Field(default=0.5, ge=0.0, le=1.0)
    voice_similarity: float = Field(default=0.75, ge=0.0, le=1.0)
    # If set, skip voice cloning and use this ElevenLabs voice ID.
    # Set to null (default) to capture and clone the speaker's voice.
    use_premade_voice_id: str | None = None
    # Last-used cloned voice ID (auto-selected on startup).
    default_voice_id: str | None = None
    # Automatically clone the speaker's voice when translation starts.
    auto_clone_voice: bool = True


class TranslationConfig(BaseModel):
    """Translation configuration defaults."""

    default_target_language: str = "en"
    preserve_context: bool = True


class UIConfig(BaseModel):
    """User interface preferences."""

    minimize_to_tray: bool = True
    show_audio_meters: bool = True
    dark_mode: bool = True
    window_x: int | None = None
    window_y: int | None = None
    window_width: int = 800
    window_height: int = 600

    # Detachable dubbed window settings
    dubbed_font_size: int = Field(default=14, ge=8, le=48)
    dubbed_opacity: float = Field(default=1.0, ge=0.2, le=1.0)
    dubbed_text_opacity: float = Field(default=1.0, ge=0.2, le=1.0)
    dubbed_window_x: int | None = None
    dubbed_window_y: int | None = None
    dubbed_window_width: int = 500
    dubbed_window_height: int = 300
    dubbed_window_detached: bool = False


class AppSettings(BaseModel):
    """Complete application settings."""

    audio: AudioConfig = Field(default_factory=AudioConfig)
    voice_clone: VoiceCloneConfig = Field(default_factory=VoiceCloneConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    ui: UIConfig = Field(default_factory=UIConfig)

    # Advanced settings
    enable_debug_logging: bool = False
    max_latency_warning_ms: int = Field(default=2000, ge=500, le=10000)

    # API keys are stored separately for security
    _elevenlabs_api_key: str | None = None
    _openai_api_key: str | None = None

    def get_elevenlabs_api_key(self) -> str | None:
        """Get ElevenLabs API key (env, then in-memory, then keyring)."""
        key = os.environ.get("ELEVENLABS_API_KEY")
        if key:
            return key
        if self._elevenlabs_api_key:
            return self._elevenlabs_api_key
        try:
            import keyring
            key = keyring.get_password("LiveDubbing", "elevenlabs_api_key")
            return key
        except Exception:
            return None

    def set_elevenlabs_api_key(self, api_key: str) -> None:
        """Store ElevenLabs API key securely."""
        self._elevenlabs_api_key = api_key
        try:
            import keyring

            keyring.set_password("LiveDubbing", "elevenlabs_api_key", api_key)
        except Exception as e:
            logger.warning("Failed to store API key in keyring", error=str(e))

    def get_openai_api_key(self) -> str | None:
        """Get OpenAI API key (env, then in-memory, then keyring)."""
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            return key
        if self._openai_api_key:
            return self._openai_api_key
        try:
            import keyring
            key = keyring.get_password("LiveDubbing", "openai_api_key")
            return key
        except Exception:
            return None

    def set_openai_api_key(self, api_key: str) -> None:
        """Store OpenAI API key securely."""
        self._openai_api_key = api_key
        try:
            import keyring
            keyring.set_password("LiveDubbing", "openai_api_key", api_key)
        except Exception as e:
            logger.warning("Failed to store OpenAI key in keyring", error=str(e))

    def set_openai_api_key_from_env(self) -> None:
        """Load OpenAI key from .env then OPENAI_API_KEY env (in-memory)."""
        _load_env_file()
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            self._openai_api_key = key

    def set_elevenlabs_api_key_from_env(self) -> None:
        """Load ElevenLabs key from .env then ELEVENLABS_API_KEY env (in-memory)."""
        _load_env_file()
        key = os.environ.get("ELEVENLABS_API_KEY")
        if key:
            self._elevenlabs_api_key = key


class ConfigManager:
    """Manage configuration file I/O."""

    def __init__(self, app_name: str = "LiveDubbing") -> None:
        """Create config manager for the given app name."""
        self.config_dir = Path(user_config_dir(app_name))
        self.config_file = self.config_dir / "settings.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppSettings:
        """Load settings from file or return defaults."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                settings = AppSettings.model_validate(data)
                logger.info("Settings loaded", path=str(self.config_file))
                return settings
            except Exception as e:
                logger.warning(
                    "Failed to load config, using defaults",
                    error=str(e),
                    path=str(self.config_file),
                )
                return AppSettings()

        logger.info("No config file found, using defaults")
        return AppSettings()

    def save(self, settings: AppSettings) -> None:
        """Save settings to file."""
        try:
            # Exclude internal fields from saved config
            data = settings.model_dump(
                exclude={"_elevenlabs_api_key", "_openai_api_key"},
                exclude_none=False,
            )
            self.config_file.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
            logger.info("Settings saved", path=str(self.config_file))
        except Exception as e:
            logger.exception("Failed to save settings", error=str(e))

    def reset(self) -> AppSettings:
        """Reset settings to defaults."""
        settings = AppSettings()
        self.save(settings)
        return settings
