"""Pydantic response models for all API endpoints."""

from pydantic import BaseModel


# ── Auth ────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class AuthResponse(BaseModel):
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
    user_id: str
    email: str
    tier: str
    subscription_status: str
    usage: UsageSnapshot


# ── Proxy ────────────────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language_code: str
    confidence: float | None = None


class TranslationResponse(BaseModel):
    translated_text: str
    source_language: str


class CloneVoiceResponse(BaseModel):
    voice_id: str
    name: str


class VoiceItem(BaseModel):
    voice_id: str
    name: str
    category: str


# ── Billing ──────────────────────────────────────────────────────────────────

class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class PlanInfo(BaseModel):
    tier: str
    price_monthly_usd: float
    dubbing_minutes: int
    tts_chars: int
    voice_clones: int
    stripe_price_id: str | None


# ── Errors ──────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    message: str
    upgrade_url: str | None = None


# Fix forward reference
AuthResponse.model_rebuild()
