"""
Synchronous PayPal checkout helpers for the desktop app.

These call the Live Translate backend's public PayPal endpoints (no auth needed):
  - GET  /api/v1/paypal/config
  - POST /api/v1/paypal/subscriptions   (starter | pro)
  - POST /api/v1/paypal/orders          (one-time, e.g. early_adopters)

The desktop opens the returned PayPal approval URL in the system browser; the backend
webhook / order-capture provisions the user's tier in Supabase. Kept sync + stdlib-ish
(httpx) so it can run in a QThread worker off the Qt event loop.
"""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)


def fetch_config(base_url: str) -> dict:
    """Return PayPal config dict; {'configured': False} on any error."""
    url = base_url.rstrip("/") + "/api/v1/paypal/config"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except httpx.HTTPError as exc:
        logger.debug("PayPal config fetch failed", error=str(exc))
    return {"configured": False}


def create_subscription(base_url: str, email: str, tier: str) -> str:
    """Create a recurring subscription (starter|pro); return the PayPal approval URL.

    Raises RuntimeError if the backend does not return an approval URL.
    """
    url = base_url.rstrip("/") + "/api/v1/paypal/subscriptions"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json={"email": email, "tier": tier})
        resp.raise_for_status()
        approve = resp.json().get("approve_url")
    if not approve:
        raise RuntimeError("PayPal did not return an approval URL")
    return str(approve)


def create_order(base_url: str, email: str, tier: str) -> str:
    """Create a one-time order (e.g. early_adopters); return the PayPal approval URL.

    Raises RuntimeError if the backend does not return an approval link.
    """
    url = base_url.rstrip("/") + "/api/v1/paypal/orders"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json={"email": email, "tier": tier})
        resp.raise_for_status()
        links = resp.json().get("links", []) or []
    approve = next(
        (lnk.get("href") for lnk in links if lnk.get("rel") in ("approve", "payer-action")),
        None,
    )
    if not approve:
        raise RuntimeError("PayPal did not return an approval link")
    return str(approve)
