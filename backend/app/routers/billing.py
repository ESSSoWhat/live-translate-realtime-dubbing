"""Stripe billing endpoints — checkout, portal, webhook."""

from __future__ import annotations

import structlog
import stripe
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
        plans.append(PlanInfo(
            tier=row["tier"],
            price_monthly_usd=float(row["price_monthly_usd"]),
            dubbing_minutes=row["dubbing_seconds"] // 60,
            tts_chars=row["tts_chars"],
            voice_clones=row["voice_clones"],
            stripe_price_id=row.get("stripe_price_id"),
        ))
    return plans


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: dict = Depends(get_current_user),
) -> CheckoutResponse:
    """Create a Stripe Checkout session and return the URL."""
    s = _stripe()

    # Ensure Stripe customer exists
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = s.Customer.create(email=user["email"], metadata={"user_id": user["id"]})
        customer_id = customer.id
        sb = await get_supabase()
        await sb.table("users").update({"stripe_customer_id": customer_id}).eq("id", user["id"]).execute()

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
    user: dict = Depends(get_current_user),
    return_url: str = "livetranslate://account",
) -> PortalResponse:
    """Create a Stripe Customer Portal session for managing subscriptions."""
    s = _stripe()

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = s.billing_portal.Session.create(customer=customer_id, return_url=return_url)
    return PortalResponse(portal_url=session.url)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, stripe_signature: str = Header(alias="stripe-signature")) -> dict:
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
            # Determine tier from the subscription's price
            sub = stripe.Subscription.retrieve(subscription_id)
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
            # Fall back to looking up by customer ID
            result = await sb.table("users").select("id").eq("stripe_customer_id", sub.customer).single().execute()
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
                price_id = sub.items.data[0].price.id
                tier = _price_to_tier(price_id, cfg)
                await sb.table("users").update({
                    "tier": tier,
                    "subscription_status": sub.status,
                }).eq("id", user_id).execute()
                logger.info("Subscription updated", user_id=user_id, tier=tier, status=sub.status)

    elif event.type == "invoice.payment_failed":
        invoice = event.data.object
        result = await sb.table("users").select("id").eq("stripe_customer_id", invoice.customer).single().execute()
        if result.data:
            await sb.table("users").update({"subscription_status": "past_due"}).eq("id", result.data["id"]).execute()

    return {"received": True}


def _price_to_tier(price_id: str, cfg) -> str:
    if price_id == cfg.stripe_starter_price_id:
        return "starter"
    if price_id == cfg.stripe_pro_price_id:
        return "pro"
    return "free"
