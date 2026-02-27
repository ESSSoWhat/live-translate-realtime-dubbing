"""Pydantic request models for all API endpoints."""

from pydantic import BaseModel, EmailStr, Field


# ── Auth ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


# ── Proxy ────────────────────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    text: str = Field(max_length=5000)
    voice_id: str
    model_id: str = "eleven_flash_v2_5"
    stability: float = Field(default=0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)


class TranslateRequest(BaseModel):
    text: str = Field(max_length=10000)
    target_language: str
    source_language: str = "auto"


class CloneVoiceRequest(BaseModel):
    name: str = Field(max_length=100)
    description: str = Field(default="", max_length=500)


# ── Billing ──────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    price_id: str
    success_url: str = "livetranslate://subscription/success"
    cancel_url: str = "livetranslate://subscription/cancel"
