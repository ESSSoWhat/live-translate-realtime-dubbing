"""User profile and usage endpoints."""

import structlog
from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.requests import UsageReportRequest
from app.models.responses import UserProfile, UsageSnapshot, UsageWithTier
from app.services.usage import get_usage_snapshot, record_usage

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/user", tags=["user"])


@router.post("/usage/report")
async def report_usage(
    body: UsageReportRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Record usage from direct API mode (retroactive; no quota check)."""
    await record_usage(str(user["id"]), body.event_type, body.quantity)
    logger.info("usage_reported", user_id=user["id"], event_type=body.event_type, quantity=body.quantity)
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
async def get_me(user: dict = Depends(get_current_user)) -> UserProfile:
    usage_data = await get_usage_snapshot(user["id"])
    return UserProfile(
        user_id=user["id"],
        email=user["email"],
        tier=user["tier"],
        subscription_status=user.get("subscription_status", "active"),
        usage=UsageSnapshot(**usage_data),
    )


@router.get("/usage", response_model=UsageWithTier)
async def get_usage(user: dict = Depends(get_current_user)) -> UsageWithTier:
    usage_data = await get_usage_snapshot(str(user["id"]))
    return UsageWithTier(tier=user["tier"], **usage_data)
