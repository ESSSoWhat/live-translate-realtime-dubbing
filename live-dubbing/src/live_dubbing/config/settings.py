"""Application settings and configuration management."""

import contextlib
import json
import os
import re
from enum import Enum
from pathlib import Path

import structlog
from platformdirs import user_config_dir
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


def redact_secrets(text: str) -> str:
    """Replace API keys, tokens, and other secrets in strings (e.g. error messages)."""
    if not text:
        return text
    # JWT (three base64url segments)
    text = re.sub(
        r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        "[REDACTED]",
        text,
    )
    # OpenAI-style sk- keys
    text = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED]", text)
    # Generic long hex/alnum strings that might be API keys (e.g. 32+ chars)
    text = re.sub(r"\b[a-fA-F0-9]{32,}\b", "[REDACTED]", text)
    return text


def _load_env_file() -> None:
    """Load API keys and Supabase config from .env if not set.

    Searches (in order): next to the running executable (for installed apps),
    current working directory, package root, and one level up.
    """
    import sys

    wanted = {
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "LIVE_TRANSLATE_API_KEY",
        "LIVE_TRANSLATE_SUPABASE_URL",
        "LIVE_TRANSLATE_SUPABASE_ANON_KEY",
    }
    missing = [k for k in wanted if not os.environ.get(k)]
    if not missing:
        return

    # Directories to search for .env
    pkg_root = Path(__file__).resolve().parents[2]
    exe_dir = Path(sys.executable).resolve().parent
    search_dirs = [
        exe_dir,  # next to exe (installed app)
        exe_dir / "_internal",  # PyInstaller one-dir mode puts datas here
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
    output_play_as_mic: bool = False  # Route TTS to CABLE Input for Zoom/Discord
    capture_device_id: str | None = None  # Volume mixer channel to capture from
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    chunk_size_ms: int = Field(default=100, ge=50, le=500)
    buffer_size_ms: int = Field(default=500, ge=100, le=2000)
    output_volume: float = Field(default=1.0, ge=0.0, le=1.0)


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
    # When True, use direct ElevenLabs API even when signed in (for offline / flaky network).
    # Usage is not tracked when this is enabled.
    prefer_direct_api: bool = True

    # Backend URL (can be overridden by LIVE_TRANSLATE_BACKEND_URL env var)
    backend_base_url: str = "https://livetranslatedubtool-production.up.railway.app"
    # Website for upgrade, account, and help (LIVE_TRANSLATE_WEBSITE_URL env override)
    website_base_url: str = "https://www.livetranslate.net"

    # API keys are stored separately for security
    _elevenlabs_api_key: str | None = None
    _openai_api_key: str | None = None

    # Auth tokens (stored in Windows Credential Manager via keyring)
    _access_token: str | None = None
    _refresh_token: str | None = None

    def get_backend_url(self) -> str:
        """Return backend URL, allowing env override for dev/testing."""
        return os.environ.get("LIVE_TRANSLATE_BACKEND_URL", self.backend_base_url)

    def get_website_url(self) -> str:
        """Return website base URL (e.g. for Open Website menu)."""
        return os.environ.get("LIVE_TRANSLATE_WEBSITE_URL", self.website_base_url).rstrip("/")

    def get_upgrade_url(self) -> str:
        """Return upgrade/subscription page URL on the website."""
        return f"{self.get_website_url()}/upgrade"

    def get_signin_url(self) -> str:
        """Return sign-in / registration page URL on the website."""
        return f"{self.get_website_url()}/login"

    def get_account_url(self) -> str:
        """Return account/dashboard page URL on the website.

        Override via LIVE_TRANSLATE_ACCOUNT_PATH if Wix uses a different path.
        """
        path = os.environ.get("LIVE_TRANSLATE_ACCOUNT_PATH", "/account-settings").strip()
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.get_website_url()}{path}"

    def get_wix_api_key_page_url(self) -> str:
        """Return Wix page URL for API key + SSO redirect (Velo). Default path ``/api-key``."""
        path = os.environ.get("LIVE_TRANSLATE_WIX_API_KEY_PATH", "/api-key").strip()
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.get_website_url()}{path}"

    def get_wix_sso_entry_url(self, redirect_uri: str) -> str:
        """Return URL to open for Wix SSO.

        Login bar (default): Opens homepage with ?sso_return=... so the user sees the full
        site and uses the login bar (Sign in in header). homepage-sso.js stores the path
        for the login page. Set LIVE_TRANSLATE_WIX_SSO_VIA_LOGIN=1 for /login directly.

        app-auth path: If LIVE_TRANSLATE_WIX_SSO_VIA_LOGIN=0 and app-auth is set, uses
        /app-auth which redirects to /api-key?redirect_uri=...
        """
        import urllib.parse

        base = self.get_website_url()
        api_key_path = os.environ.get("LIVE_TRANSLATE_WIX_API_KEY_PATH", "/api-key").strip()
        if not api_key_path.startswith("/"):
            api_key_path = "/" + api_key_path
        # Use /app-auth by default (preserves redirect_uri through login). Set to "" to use /api-key directly.
        # Login-bar: open homepage so user uses the site's login bar (Sign in / Log in in header)
        # Passes sso_return via query; homepage-sso.js stores it for the login page.
        # Set LIVE_TRANSLATE_WIX_SSO_VIA_LOGIN=1 to open /login directly instead.
        use_login_bar = os.environ.get("LIVE_TRANSLATE_WIX_SSO_VIA_LOGIN", "0").strip() in ("0", "false", "no")
        if use_login_bar:
            destination = f"{api_key_path}?{urllib.parse.urlencode({'redirect_uri': redirect_uri})}"
            sso_return = urllib.parse.quote(destination, safe="")
            return f"{base}/?sso_return={sso_return}"

        # Login-first: open /login directly (dedicated login page)
        use_login_first = os.environ.get("LIVE_TRANSLATE_WIX_SSO_VIA_LOGIN", "0").strip() in ("1", "true", "yes")
        if use_login_first:
            destination = f"{api_key_path}?{urllib.parse.urlencode({'redirect_uri': redirect_uri})}"
            return_url = urllib.parse.quote(destination, safe="")
            return f"{base}/login?returnUrl={return_url}"

        # app-auth: public page that forwards to api-key with redirect_uri
        app_auth_path = os.environ.get("LIVE_TRANSLATE_WIX_APP_AUTH_PATH", "/app-auth").strip()
        if app_auth_path.startswith("/"):
            sso_params = urllib.parse.urlencode({"redirect_uri": redirect_uri})
            return f"{base}{app_auth_path}?{sso_params}"
        sso_params = urllib.parse.urlencode({"redirect_uri": redirect_uri})
        return f"{base}{api_key_path}?{sso_params}"

    def get_download_url(self) -> str:
        """Return app download page URL on the website."""
        return f"{self.get_website_url()}/download"

    def get_access_token(self) -> str | None:
        """Get JWT access token from in-memory cache or keyring."""
        if self._access_token:
            return self._access_token
        try:
            import keyring
            return keyring.get_password("LiveDubbing", "access_token")
        except Exception:
            return None

    def get_refresh_token(self) -> str | None:
        """Get JWT refresh token from in-memory cache or keyring."""
        if self._refresh_token:
            return self._refresh_token
        try:
            import keyring
            return keyring.get_password("LiveDubbing", "refresh_token")
        except Exception:
            return None

    def set_auth_tokens(self, access_token: str, refresh_token: str) -> None:
        """Store both JWT tokens in memory and Windows Credential Manager."""
        self._access_token = access_token
        self._refresh_token = refresh_token
        try:
            import keyring
            keyring.set_password("LiveDubbing", "access_token", access_token)
            keyring.set_password("LiveDubbing", "refresh_token", refresh_token)
        except Exception as e:
            logger.warning("Failed to store auth tokens in keyring", error=redact_secrets(str(e)))

    def set_cached_user_info(self, user_id: str, tier: str) -> None:
        """Store user_id and tier in keyring for use when resuming with valid token."""
        try:
            import keyring
            keyring.set_password("LiveDubbing", "auth_user_id", user_id)
            keyring.set_password("LiveDubbing", "auth_tier", tier)
        except Exception as e:
            logger.warning("Failed to store cached user info in keyring", error=redact_secrets(str(e)))

    def get_cached_auth_response(self) -> dict:
        """Return a minimal auth dict (user_id, tier, usage=None) from keyring when token is valid."""
        try:
            import keyring
            user_id = keyring.get_password("LiveDubbing", "auth_user_id") or ""
            tier = keyring.get_password("LiveDubbing", "auth_tier") or "free"
            return {"user_id": user_id, "tier": tier, "usage": None}
        except Exception:
            return {}

    def clear_auth_tokens(self) -> None:
        """Remove stored tokens on logout."""
        self._access_token = None
        self._refresh_token = None
        try:
            import keyring
            keyring.delete_password("LiveDubbing", "access_token")
            keyring.delete_password("LiveDubbing", "refresh_token")
            keyring.delete_password("LiveDubbing", "auth_user_id")
            keyring.delete_password("LiveDubbing", "auth_tier")
        except Exception:
            pass

    def is_token_valid(self) -> bool:
        """Quick check: token exists; for JWT also check exp; for API key (non-JWT) consider valid."""
        token = self.get_access_token()
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3 or len(token) < 20:
            # API key (from Wix): no expiry check; backend will reject if invalid
            return True
        try:
            import base64
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            import time
            return payload.get("exp", 0) > time.time() + 300
        except Exception:
            return False

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
            logger.warning("Failed to store API key in keyring", error=redact_secrets(str(e)))

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
            logger.warning("Failed to store OpenAI key in keyring", error=redact_secrets(str(e)))

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

    def set_auth_token_from_env(self) -> None:
        """Load backend API key from LIVE_TRANSLATE_API_KEY env and store as auth token."""
        _load_env_file()
        key = os.environ.get("LIVE_TRANSLATE_API_KEY")
        if key:
            self.set_auth_tokens(key.strip(), "")


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
                    error=redact_secrets(str(e)),
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
            logger.exception("Failed to save settings", error=redact_secrets(str(e)))

    def reset(self) -> AppSettings:
        """Reset settings to defaults."""
        settings = AppSettings()
        self.save(settings)
        return settings
