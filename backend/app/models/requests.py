"""Pydantic request models for all API endpoints."""

import re
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, field_validator


# Allowed URL schemes for checkout redirects (no open redirects)
_ALLOWED_REDIRECT_PATTERN = re.compile(
    r"^(livetranslate://[^/]+(/.*)?|https://[a-zA-Z0-9][-a-zA-Z0-9.]*[a-zA-Z0-9](/.*)?)$"
)

# Only these hosts are allowed for https redirects; livetranslate:// is always allowed
TRUSTED_REDIRECT_HOSTS: frozenset[str] = frozenset({
    "livetranslate.app",
    "www.livetranslate.app",
    "livetranslate.net",
    "www.livetranslate.net",
})


def _is_allowed_redirect_url(url: str) -> bool:
    """Return True if url is a safe success_url/cancel_url (no open redirect)."""
    if not url or not _ALLOWED_REDIRECT_PATTERN.match(url):
        return False
    parsed = urlparse(url)
    if parsed.scheme == "livetranslate":
        return True
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in TRUSTED_REDIRECT_HOSTS:
        return True
    return any(host == h or host.endswith("." + h) for h in TRUSTED_REDIRECT_HOSTS)


# ── Auth ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """Request body for email/password login."""

    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    """Request body for new user registration."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=1024)


class RefreshRequest(BaseModel):
    """Request body for token refresh."""

    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    """Request body for forgot-password flow."""

    email: EmailStr


# ── Proxy ────────────────────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    """Request body for TTS synthesis."""

    text: str = Field(max_length=5000)
    voice_id: str
    model_id: str = "eleven_flash_v2_5"
    stability: float = Field(default=0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)


class TranslateRequest(BaseModel):
    """Request body for text translation."""

    text: str = Field(max_length=10000)
    target_language: str
    source_language: str = "auto"


class CloneVoiceRequest(BaseModel):
    """Request body for voice cloning."""

    name: str = Field(max_length=100)
    description: str = Field(default="", max_length=500)


# ── Billing ──────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    """Request body for creating a Stripe checkout session."""

    price_id: str
    success_url: str = "livetranslate://subscription/success"
    cancel_url: str = "livetranslate://subscription/cancel"

    @field_validator("success_url", "cancel_url")
    @classmethod
    def validate_redirect_url(cls, v: str) -> str:
        if not _is_allowed_redirect_url(v):
            raise ValueError("URL must be livetranslate:// or https:// and not an open redirect")
        return v
