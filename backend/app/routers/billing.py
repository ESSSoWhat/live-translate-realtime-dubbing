"""Stripe billing endpoints — checkout, portal, webhook."""

from __future__ import annotations

import structlog
import stripe  # pylint: disable=import-error
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.requests import CheckoutRequest
from app.models.responses import CheckoutResponse, PlanInfo, PortalResponse
from app.services.supabase_client import get_supabase

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

_TIER_LABELS = {
    "free": "Free",
    "starter": "Starter",
    "pro": "Pro",
}


def _stripe_configured() -> bool:
    """Return True if Stripe is configured (secret key is non-empty)."""
    cfg = get_settings()
    return bool(cfg.stripe_secret_key and cfg.stripe_secret_key.strip())


def _stripe() -> stripe.Stripe:
    cfg = get_settings()
    stripe.api_key = cfg.stripe_secret_key
    return stripe  # type: ignore[return-value]


@router.get("/plans", response_model=list[PlanInfo])
async def list_plans() -> list[PlanInfo]:
    """Return all available subscription tiers."""
    sb = await get_supabase()
    result = await sb.table("tier_limits").select("*").neq("tier", "free").execute()
    plans = []
    for row in result.data:
        dubbing_seconds = row.get("dubbing_seconds") or 0
        price_monthly_usd = float(row.get("price_monthly_usd") or 0.0)
        tts_chars = row.get("tts_chars") or 0
        voice_clones = row.get("voice_clones") or 0
        plans.append(PlanInfo(
            tier=row["tier"],
            price_monthly_usd=price_monthly_usd,
            dubbing_seconds=int(dubbing_seconds),
            tts_chars=int(tts_chars),
            voice_clones=int(voice_clones),
            stripe_price_id=row.get("stripe_price_id"),
        ))
    return plans


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> CheckoutResponse:
    """Create a Stripe Checkout session and return the URL."""
    if not _stripe_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured. Set STRIPE_SECRET_KEY and related Stripe env vars.",
        )
    cfg = get_settings()
    s = _stripe()

    allowed_prices = {x for x in (cfg.stripe_starter_price_id, cfg.stripe_pro_price_id) if x}
    if body.price_id not in allowed_prices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid price_id",
        )

    # Ensure Stripe customer exists (idempotent + atomic update)
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        idempotency_key = f"user-{user['id']}"
        customer = s.Customer.create(
            email=user["email"],
            metadata={"user_id": user["id"]},
            idempotency_key=idempotency_key,
        )
        customer_id = customer.id
        sb = await get_supabase()
        update_result = (
            await sb.table("users")
            .update({"stripe_customer_id": customer_id})
            .eq("id", user["id"])
            .is_("stripe_customer_id", "null")
            .execute()
        )
        if not update_result.data:
            # Another request already set it; use existing customer
            existing = await sb.table("users").select("stripe_customer_id").eq("id", user["id"]).maybe_single().execute()
            if existing.data and existing.data.get("stripe_customer_id"):
                customer_id = existing.data["stripe_customer_id"]

    session = s.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": body.price_id, "quantity": 1}],
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        client_reference_id=user["id"],
        subscription_data={"metadata": {"user_id": user["id"]}},
    )

    return CheckoutResponse(checkout_url=session.url)


@router.get("/portal", response_model=PortalResponse)
async def customer_portal(
    user: dict = Depends(get_current_user),  # noqa: B008
    return_url: str = "livetranslate://account",
) -> PortalResponse:
    """Create a Stripe Customer Portal session for managing subscriptions."""
    if not _stripe_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured. Set STRIPE_SECRET_KEY and related Stripe env vars.",
        )
    s = _stripe()

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = s.billing_portal.Session.create(customer=customer_id, return_url=return_url)
    return PortalResponse(portal_url=session.url)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, stripe_signature: str = Header(alias="stripe-signature")) -> dict:  # noqa: B008
    """Handle Stripe webhook events to update subscription status."""
    cfg = get_settings()
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, cfg.stripe_webhook_secret)
    except stripe.error.SignatureVerificationError as exc:
        logger.warning("Invalid Stripe webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    sb = await get_supabase()

    if event.type == "checkout.session.completed":
        session = event.data.object
        user_id = session.get("client_reference_id")
        subscription_id = session.get("subscription")
        if user_id and subscription_id:
            s = _stripe()
            sub = s.Subscription.retrieve(subscription_id)
            if not sub.items or not getattr(sub.items, "data", None) or len(sub.items.data) == 0:
                logger.warning(
                    "Subscription has no items",
                    subscription_id=subscription_id,
                    user_id=user_id,
                )
                await sb.table("users").update({
                    "tier": "free",
                    "subscription_status": "no_items",
                }).eq("id", user_id).execute()
            else:
                price_id = sub.items.data[0].price.id
                tier = _price_to_tier(price_id, cfg)
                await sb.table("users").update({
                    "tier": tier,
                    "subscription_id": subscription_id,
                    "subscription_status": "active",
                }).eq("id", user_id).execute()
                logger.info("Subscription activated", user_id=user_id, tier=tier)

    elif event.type in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub = event.data.object
        user_id = sub.metadata.get("user_id")
        if not user_id:
            result = await sb.table("users").select("id").eq("stripe_customer_id", sub.customer).maybe_single().execute()
            user_id = result.data["id"] if result.data else None

        if user_id:
            if event.type == "customer.subscription.deleted" or sub.status in ("canceled", "unpaid"):
                await sb.table("users").update({
                    "tier": "free",
                    "subscription_status": "canceled",
                    "subscription_id": None,
                }).eq("id", user_id).execute()
                logger.info("Subscription canceled — downgraded to free", user_id=user_id)
            else:
                if not sub.items or not getattr(sub.items, "data", None) or len(sub.items.data) == 0:
                    logger.warning("Subscription updated but no items", subscription_id=getattr(sub, "id", None))
                else:
                    price_id = sub.items.data[0].price.id
                    tier = _price_to_tier(price_id, cfg)
                    await sb.table("users").update({
                        "tier": tier,
                        "subscription_status": sub.status,
                    }).eq("id", user_id).execute()
                    logger.info("Subscription updated", user_id=user_id, tier=tier, status=sub.status)

    elif event.type == "invoice.payment_failed":
        invoice = event.data.object
        logger.info("invoice.payment_failed received", invoice_id=getattr(invoice, "id", None), customer=invoice.customer)
        result = await sb.table("users").select("id").eq("stripe_customer_id", invoice.customer).maybe_single().execute()
        if result.data:
            await sb.table("users").update({"subscription_status": "past_due"}).eq("id", result.data["id"]).execute()

    return {"received": True}


def _price_to_tier(price_id: str, cfg) -> str:
    if price_id == cfg.stripe_starter_price_id:
        return "starter"
    if price_id == cfg.stripe_pro_price_id:
        return "pro"
    if price_id:
        logger.warning(
            "Unknown Stripe price_id — returning free",
            price_id=price_id,
            stripe_starter_price_id=cfg.stripe_starter_price_id or "(empty)",
            stripe_pro_price_id=cfg.stripe_pro_price_id or "(empty)",
        )
    return "free"
