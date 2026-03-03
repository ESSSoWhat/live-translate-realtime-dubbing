"""Pydantic response models for all API endpoints."""

from pydantic import BaseModel, computed_field


# ── Auth ────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """Auth token pair and metadata."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class AuthResponse(BaseModel):
    """Full auth response with user and usage snapshot."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    email: str
    tier: str
    usage: "UsageSnapshot"


# ── Usage ────────────────────────────────────────────────────────────────────

class UsageSnapshot(BaseModel):
    """Current period usage and limits."""

    dubbing_seconds_used: int
    dubbing_seconds_limit: int
    tts_chars_used: int
    tts_chars_limit: int
    stt_seconds_used: int
    stt_seconds_limit: int
    voice_clones_used: int
    voice_clones_limit: int
    period_reset_date: str  # ISO date string


# ── User ────────────────────────────────────────────────────────────────────

class UserProfile(BaseModel):
    """User profile with subscription and usage."""

    user_id: str
    email: str
    tier: str
    subscription_status: str
    usage: UsageSnapshot


# ── Proxy ────────────────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    """STT result with optional confidence."""

    text: str
    language_code: str
    confidence: float | None = None


class TranslationResponse(BaseModel):
    """Translated text and source language."""

    translated_text: str
    source_language: str


class CloneVoiceResponse(BaseModel):
    """Created voice id and name."""

    voice_id: str
    name: str


class VoiceItem(BaseModel):
    """Voice list item."""

    voice_id: str
    name: str
    category: str


# ── Billing ──────────────────────────────────────────────────────────────────

class CheckoutResponse(BaseModel):
    """Stripe checkout session URL."""

    checkout_url: str


class PortalResponse(BaseModel):
    """Stripe customer portal URL."""

    portal_url: str


class PlanInfo(BaseModel):
    """Plan tier and limits for display."""

    tier: str
    price_monthly_usd: float
    dubbing_seconds: int
    tts_chars: int
    voice_clones: int
    stripe_price_id: str | None

    @computed_field(deprecated=True)
    @property
    def dubbing_minutes(self) -> int:
        """Deprecated. Use dubbing_seconds. Kept for backward compatibility."""
        return self.dubbing_seconds // 60


# ── Errors ──────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Structured error with optional upgrade URL."""

    error: str
    message: str
    upgrade_url: str | None = None


# Fix forward reference
AuthResponse.model_rebuild()
