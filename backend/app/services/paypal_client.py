"""PayPal REST API client (current v1/v2 REST; the Python SDK is deprecated).

Server-side OAuth2 client-credentials + helpers for Orders, Subscriptions and
webhook signature verification. All config comes from environment variables.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_LIVE = "https://api-m.paypal.com"
_SANDBOX = "https://api-m.sandbox.paypal.com"

_token: str | None = None
_token_exp: float = 0.0


class PayPalNotConfiguredError(RuntimeError):
    """Raised when PayPal credentials are absent."""


def paypal_configured() -> bool:
    cfg = get_settings()
    return bool((cfg.paypal_client_id or "").strip() and (cfg.paypal_client_secret or "").strip())


def base_url() -> str:
    return _SANDBOX if get_settings().paypal_env.strip().lower() == "sandbox" else _LIVE


async def get_access_token() -> str:
    """Return a cached OAuth2 access token, refreshing when near expiry."""
    global _token, _token_exp  # pylint: disable=global-statement
    if not paypal_configured():
        raise PayPalNotConfiguredError("PayPal not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET).")
    if _token and time.time() < _token_exp - 60:
        return _token

    cfg = get_settings()
    creds = base64.b64encode(f"{cfg.paypal_client_id}:{cfg.paypal_client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url()}/v1/oauth2/token",
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_exp = time.time() + int(data.get("expires_in", 3200))
    return _token


async def api(method: str, path: str, json: dict | None = None) -> dict:
    """Authenticated PayPal REST call. Returns parsed JSON ({} for empty bodies)."""
    token = await get_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method,
            f"{base_url()}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=json,
        )
    if resp.status_code >= 400:
        logger.error("PayPal API error", path=path, status=resp.status_code, body=resp.text[:500])
    resp.raise_for_status()
    return resp.json() if resp.content else {}


async def verify_webhook_signature(headers: dict, body: dict) -> bool:
    """Verify a webhook event using PayPal's verify-webhook-signature endpoint."""
    cfg = get_settings()
    webhook_id = (cfg.paypal_webhook_id or "").strip()
    if not webhook_id:
        logger.warning("PAYPAL_WEBHOOK_ID not set — cannot verify webhook signature")
        return False
    payload = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": webhook_id,
        "webhook_event": body,
    }
    try:
        result = await api("POST", "/v1/notifications/verify-webhook-signature", json=payload)
    except httpx.HTTPError as exc:
        logger.error("Webhook verification call failed", exc_info=exc)
        return False
    return result.get("verification_status") == "SUCCESS"
