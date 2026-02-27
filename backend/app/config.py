"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_service_role_key: str
    supabase_jwt_secret: str

    # ElevenLabs (server-side only)
    elevenlabs_api_key: str

    # OpenAI
    openai_api_key: str = ""

    # Stripe (optional — billing endpoints are disabled when these are absent)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_pro_price_id: str = ""

    # App
    backend_env: str = "development"
    backend_cors_origins: str = "*"

    @property
    def is_production(self) -> bool:
        return self.backend_env == "production"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
