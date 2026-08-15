"""User profile and usage endpoints."""

import structlog
from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.requests import UsageReportRequest
from app.models.responses import UserProfile, UsageSnapshot, UsageWithTier
from app.services.usage import get_usage_snapshot, record_usage
from app.services.wix_plans import refresh_user_tier_from_wix

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/user", tags=["user"])


@router.post("/usage/report")
async def report_usage(
    body: UsageReportRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Record usage from direct API mode (retroactive; no quota check)."""
    user_id = str(user["id"])
    await record_usage(user_id, body.event_type, body.quantity)
    logger.info("usage_reported", user_id=user_id, event_type=body.event_type, quantity=body.quantity)
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
async def get_me(user: dict = Depends(get_current_user)) -> UserProfile:
    user = await refresh_user_tier_from_wix(user)
    user_id = str(user["id"])
    usage_data = await _usage_or_default(user_id, user["tier"])
    return UserProfile(
        user_id=user_id,
        email=user["email"],
        tier=user["tier"],
        subscription_status=user.get("subscription_status", "active"),
        usage=UsageSnapshot(**usage_data),
    )


@router.get("/usage", response_model=UsageWithTier)
async def get_usage(user: dict = Depends(get_current_user)) -> UsageWithTier:
    user = await refresh_user_tier_from_wix(user)
    user_id = str(user["id"])
    usage_data = await _usage_or_default(user_id, user["tier"])
    return UsageWithTier(tier=user["tier"], **usage_data)


async def _usage_or_default(user_id: str, tier: str) -> dict:
    """Return the live usage snapshot; fall back to zeroed defaults if the metering DB
    (SUPABASE_DB_URL) is unavailable so the profile endpoints never hard-crash (500)."""
    try:
        return await get_usage_snapshot(user_id)
    except Exception as e:  # noqa: BLE001 — any DB/config error must degrade, not 500
        from app.routers.auth import _default_usage
        logger.warning("usage snapshot unavailable; returning defaults", user_id=user_id, error=str(e))
        return _default_usage(tier)
