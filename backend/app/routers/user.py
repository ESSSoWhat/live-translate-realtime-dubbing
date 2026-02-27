"""User profile and usage endpoints."""

from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.responses import UserProfile, UsageSnapshot
from app.services.usage import get_usage_snapshot

router = APIRouter(prefix="/user", tags=["user"])


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


@router.get("/usage", response_model=UsageSnapshot)
async def get_usage(user: dict = Depends(get_current_user)) -> UsageSnapshot:
    usage_data = await get_usage_snapshot(user["id"])
    return UsageSnapshot(**usage_data)
