"""Lightweight analytics for the upgrade-nudge A/B test.

Clients pick a stable variant per user ("a" | "b") and POST /analytics/nudge with
action "shown" (on display) and "clicked" (on Upgrade tap). GET /analytics/nudge/stats
(admin) returns shown/clicked/conversion per variant so we can see which copy converts.
"""

from __future__ import annotations

import structlog  # pylint: disable=import-error
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings
from app.services.supabase_client import get_supabase

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])

_VARIANTS = {"a", "b"}
_ACTIONS = {"shown", "clicked"}


class NudgeEvent(BaseModel):
    variant: str            # "a" | "b"
    action: str             # "shown" | "clicked"
    feature: str | None = None   # minutes | text-to-speech | translation
    surface: str | None = None   # mobile | wix | desktop


def _require_admin(x_admin_secret: str | None) -> None:
    secret = (get_settings().lt_sync_secret or "").strip()
    if not secret or x_admin_secret != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin secret")


@router.post("/nudge", status_code=status.HTTP_201_CREATED)
async def record_nudge(event: NudgeEvent) -> dict:
    """Record a nudge impression or click for the A/B test (best-effort)."""
    if event.variant not in _VARIANTS or event.action not in _ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid variant or action")
    try:
        sb = await get_supabase()
        await sb.table("nudge_events").insert({
            "variant": event.variant,
            "action": event.action,
            "feature": event.feature,
            "surface": event.surface,
        }).execute()
        return {"recorded": True}
    except Exception as exc:  # noqa: BLE001
        # Analytics is non-critical (e.g. nudge_events table not created yet).
        logger.warning("Nudge event not recorded", error=str(exc))
        return {"recorded": False}


@router.get("/nudge/stats")
async def nudge_stats(x_admin_secret: str | None = Header(default=None)) -> dict:
    """Admin: conversion (clicked/shown) per nudge variant."""
    _require_admin(x_admin_secret)
    sb = await get_supabase()
    rows = (await sb.table("nudge_events").select("variant", "action").execute()).data or []
    stats: dict[str, dict] = {}
    for v in ("a", "b"):
        shown = sum(1 for r in rows if r.get("variant") == v and r.get("action") == "shown")
        clicked = sum(1 for r in rows if r.get("variant") == v and r.get("action") == "clicked")
        stats[v] = {
            "shown": shown,
            "clicked": clicked,
            "conversion": round(clicked / shown, 4) if shown else None,
        }
    return {"variants": stats}
