"""PayPal payment endpoints — one-time Orders + recurring Subscriptions.

Coexists with Wix billing: on successful payment/subscription, the user's tier is
provisioned in Supabase via the shared `provision_or_update_user` helper.
"""

from __future__ import annotations

import structlog  # pylint: disable=import-error
import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from app.config import get_settings
from app.routers.billing import provision_or_update_user
from app.services import paypal_client as pp
from app.services.supabase_client import get_supabase

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/paypal", tags=["paypal"])

# Server-side one-time prices (AUD). Client cannot set the amount. Edit as needed.
_ONE_TIME_PRICES = {"early_adopters": "149.00"}
_SUBSCRIPTION_TIERS = {"starter", "pro"}

_ACTIVATE_EVENTS = ("PAYMENT.CAPTURE.COMPLETED", "BILLING.SUBSCRIPTION.ACTIVATED")
_DEACTIVATE_EVENTS = (
    "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.EXPIRED",
    "BILLING.SUBSCRIPTION.SUSPENDED",
)


async def _claim_webhook_event(event_id: str, event_type: str) -> bool:
    """Atomically claim a PayPal webhook event id for processing.

    Returns True the first time an event id is seen (process it) and False if it was
    already recorded (idempotent skip). Backed by the `webhook_events` table's unique
    `event_id` constraint via INSERT ... ON CONFLICT DO NOTHING, so PayPal's automatic
    retries can never double-provision or flip a tier twice.
    """
    sb = await get_supabase()
    res = await (
        sb.table("webhook_events")
        .upsert(
            {"event_id": event_id, "event_type": event_type, "source": "paypal"},
            on_conflict="event_id",
            ignore_duplicates=True,
        )
        .execute()
    )
    return bool(res.data)


async def _release_webhook_event(event_id: str) -> None:
    """Undo a claim so PayPal's retry can reprocess after a transient provisioning failure."""
    try:
        sb = await get_supabase()
        await sb.table("webhook_events").delete().eq("event_id", event_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to release webhook event", event_id=event_id, error=str(exc))


def _require_configured() -> None:
    if not pp.paypal_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PayPal is not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.",
        )


def _require_admin(x_admin_secret: str | None) -> None:
    """Admin actions (plan creation) reuse the LT_SYNC_SECRET value as the admin key."""
    secret = (get_settings().lt_sync_secret or "").strip()
    if not secret or x_admin_secret != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin secret")


def _plan_id_for_tier(tier: str) -> str:
    cfg = get_settings()
    mapping = {"starter": cfg.paypal_starter_plan_id, "pro": cfg.paypal_pro_plan_id}
    plan_id = (mapping.get(tier) or "").strip()
    if not plan_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No PayPal plan configured for tier '{tier}'. Run POST /paypal/admin/setup-plans.",
        )
    return plan_id


class OrderRequest(BaseModel):
    email: EmailStr
    tier: str = "early_adopters"


class SubscriptionRequest(BaseModel):
    email: EmailStr
    tier: str  # starter | pro


@router.get("/config")
async def paypal_config() -> dict:
    """Public config for frontends (client id is publishable; no secret exposed)."""
    cfg = get_settings()
    return {
        "configured": pp.paypal_configured(),
        "env": cfg.paypal_env,
        "client_id": cfg.paypal_client_id,
        "currency": cfg.paypal_currency,
        "starter_plan_id": cfg.paypal_starter_plan_id,
        "pro_plan_id": cfg.paypal_pro_plan_id,
    }


@router.post("/orders")
async def create_order(body: OrderRequest) -> dict:
    """Create a one-time PayPal order for a lifetime/early-adopter purchase."""
    _require_configured()
    price = _ONE_TIME_PRICES.get(body.tier)
    if not price:
        raise HTTPException(status_code=400, detail=f"No one-time price for tier '{body.tier}'")
    cfg = get_settings()
    try:
        order = await pp.api("POST", "/v2/checkout/orders", json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": cfg.paypal_currency, "value": price},
                "custom_id": f"{body.email}|{body.tier}",
                "description": f"Live Translate {body.tier}",
            }],
        })
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="PayPal order creation failed") from exc
    return {"order_id": order.get("id"), "status": order.get("status"), "links": order.get("links", [])}


@router.post("/orders/{order_id}/capture")
async def capture_order(order_id: str) -> dict:
    """Capture an approved order; on completion, provision the user's tier."""
    _require_configured()
    try:
        result = await pp.api("POST", f"/v2/checkout/orders/{order_id}/capture")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="PayPal capture failed") from exc

    if result.get("status") == "COMPLETED":
        pu = (result.get("purchase_units") or [{}])[0]
        custom_id = pu.get("custom_id") or ""
        email, _, tier = custom_id.partition("|")
        if email and tier:
            await provision_or_update_user(email, tier, "active")
            logger.info("PayPal one-time payment provisioned", email=email, tier=tier)
    return {"status": result.get("status"), "order_id": order_id}


@router.post("/subscriptions")
async def create_subscription(body: SubscriptionRequest) -> dict:
    """Create a recurring PayPal subscription for starter/pro."""
    _require_configured()
    if body.tier not in _SUBSCRIPTION_TIERS:
        raise HTTPException(status_code=400, detail=f"Tier '{body.tier}' is not a subscription tier")
    plan_id = _plan_id_for_tier(body.tier)
    try:
        sub = await pp.api("POST", "/v1/billing/subscriptions", json={
            "plan_id": plan_id,
            "custom_id": f"{body.email}|{body.tier}",
            "subscriber": {"email_address": body.email},
        })
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="PayPal subscription creation failed") from exc
    approve = next((lnk["href"] for lnk in sub.get("links", []) if lnk.get("rel") == "approve"), None)
    return {"subscription_id": sub.get("id"), "status": sub.get("status"), "approve_url": approve}


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def paypal_webhook(request: Request) -> dict:
    """Verify PayPal webhook signature and sync tier changes into Supabase."""
    _require_configured()
    body = await request.json()
    if not await pp.verify_webhook_signature(dict(request.headers), body):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook verification failed")

    event = body.get("event_type", "")
    event_id = str(body.get("id") or "")
    resource = body.get("resource", {}) or {}
    custom_id = resource.get("custom_id") or ""
    # one-time captures carry custom_id under purchase_units
    if not custom_id:
        pu = (resource.get("purchase_units") or [{}])[0]
        custom_id = pu.get("custom_id") or ""
    email, _, tier = custom_id.partition("|")

    if not (email and tier and event in (_ACTIVATE_EVENTS + _DEACTIVATE_EVENTS)):
        logger.warning("PayPal webhook ignored", event_type=event, event_id=event_id)
        return {"received": True}

    # Idempotency: claim the event id so PayPal retries can't provision twice.
    if event_id and not await _claim_webhook_event(event_id, event):
        logger.info("PayPal webhook: duplicate ignored", event_type=event, event_id=event_id)
        return {"received": True, "duplicate": True}

    try:
        if event in _ACTIVATE_EVENTS:
            await provision_or_update_user(email, tier, "active")
            logger.info("PayPal webhook: activated", event_type=event, email=email, tier=tier)
        else:
            await provision_or_update_user(email, "free", "canceled")
            logger.info("PayPal webhook: canceled", event_type=event, email=email)
    except Exception:
        # Roll back the claim so PayPal's retry can reprocess this event.
        if event_id:
            await _release_webhook_event(event_id)
        raise
    return {"received": True}


@router.post("/admin/setup-plans")
async def setup_plans(x_admin_secret: str | None = Header(default=None)) -> dict:
    """Create the PayPal product + starter/pro billing plans; returns plan IDs to set in env."""
    _require_admin(x_admin_secret)
    _require_configured()
    cfg = get_settings()
    prices = {"starter": "9.99", "pro": "24.99"}
    try:
        product = await pp.api("POST", "/v1/catalogs/products", json={
            "name": "Live Translate",
            "type": "SERVICE",
            "category": "SOFTWARE",
        })
        product_id = product["id"]
        plan_ids = {}
        for tier, price in prices.items():
            plan = await pp.api("POST", "/v1/billing/plans", json={
                "product_id": product_id,
                "name": f"Live Translate {tier.capitalize()}",
                "billing_cycles": [{
                    "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                    "pricing_scheme": {"fixed_price": {"value": price, "currency_code": cfg.paypal_currency}},
                }],
                "payment_preferences": {"auto_bill_outstanding": True},
            })
            plan_ids[tier] = plan["id"]
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="PayPal plan setup failed") from exc

    logger.info("PayPal plans created", product_id=product_id, plans=plan_ids)
    return {
        "product_id": product_id,
        "plans": plan_ids,
        "action_required": "Set these in env: "
        f"PAYPAL_STARTER_PLAN_ID={plan_ids.get('starter')} PAYPAL_PRO_PLAN_ID={plan_ids.get('pro')}",
    }
