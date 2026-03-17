"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict  # pylint: disable=import-error


class Settings(BaseSettings):  # pylint: disable=too-few-public-methods
    """Environment-based settings for the backend API."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase (required for auth; use empty string to run without auth)
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_db_url: str = ""  # Direct PostgreSQL connection for usage tracking (asyncpg)

    # ElevenLabs (required for voice proxy; use empty string to run without)
    elevenlabs_api_key: str = ""

    # OpenAI
    openai_api_key: str = ""

    # Stripe (optional — billing endpoints are disabled when these are absent)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_pro_price_id: str = ""

    # Qonversion (optional — webhook to sync app/web subscription tier)
    qonversion_webhook_secret: str = ""

    # Wix (optional — sync subscription tier from Wix Pricing Plans; call from Velo)
    wix_sync_secret: str = ""

    # App
    backend_env: str = "development"
    backend_cors_origins: str = "*"

    @property
    def is_production(self) -> bool:
        """Return True if backend_env is production."""
        return self.backend_env == "production"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings instance; load from env on first call."""
    global _settings  # pylint: disable=global-statement
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
