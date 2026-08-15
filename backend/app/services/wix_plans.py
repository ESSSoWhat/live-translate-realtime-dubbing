"""Refresh Live Translate user tiers from Wix Pricing Plans (source of truth)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.services.supabase_client import get_supabase
from app.services.wix_tier_map import wix_plan_to_tier

logger = structlog.get_logger(__name__)

_WIX_API = "https://www.wixapis.com"
_REFRESH_TTL_SEC = 300  # 5 minutes
_refresh_cache: dict[str, float] = {}  # email.lower() -> monotonic deadline
_cached_site_id: str | None = None


def _wix_configured() -> bool:
    cfg = get_settings()
    return bool((cfg.wix_api_key or "").strip() and (cfg.wix_account_id or "").strip())


def _auth_headers(*, site_id: str | None = None) -> dict[str, str]:
    cfg = get_settings()
    headers = {
        "Authorization": cfg.wix_api_key.strip(),
        "Content-Type": "application/json",
    }
    if site_id:
        headers["wix-site-id"] = site_id
    else:
        headers["wix-account-id"] = cfg.wix_account_id.strip()
    return headers


async def _resolve_site_id(client: httpx.AsyncClient) -> str | None:
    """Return configured site id, or discover the first site for the account."""
    global _cached_site_id
    cfg = get_settings()
    configured = (cfg.wix_site_id or "").strip()
    if configured:
        return configured
    if _cached_site_id:
        return _cached_site_id

    try:
        resp = await client.post(
            f"{_WIX_API}/site-list/v2/sites/query",
            headers=_auth_headers(),
            json={"query": {"paging": {"limit": 10}}},
            timeout=20.0,
        )
        if resp.status_code >= 400:
            logger.warning("Wix site query failed", status=resp.status_code, body=resp.text[:300])
            return None
        sites = (resp.json() or {}).get("sites") or []
        if not sites:
            logger.warning("Wix site query returned no sites")
            return None
        site_id = str(sites[0].get("id") or "").strip()
        if site_id:
            _cached_site_id = site_id
            logger.info("Resolved Wix site id from account", site_id=site_id)
        return site_id or None
    except Exception as exc:
        logger.warning("Wix site resolve error", error=str(exc))
        return None


async def _find_member_id(client: httpx.AsyncClient, site_id: str, email: str) -> str | None:
    """Look up Wix member id by login email."""
    try:
        resp = await client.post(
            f"{_WIX_API}/members/v1/members/query",
            headers=_auth_headers(site_id=site_id),
            json={
                "query": {
                    "filter": {"loginEmail": {"$eq": email}},
                    "paging": {"limit": 1},
                },
                "fieldsets": ["EXTENDED"],
            },
            timeout=20.0,
        )
        if resp.status_code >= 400:
            # Some keys use contact.emails instead; try a broader filter.
            logger.warning("Wix member query failed", status=resp.status_code, body=resp.text[:300])
            return None
        members = (resp.json() or {}).get("members") or []
        if not members:
            return None
        return str(members[0].get("id") or "").strip() or None
    except Exception as exc:
        logger.warning("Wix member lookup error", email=email, error=str(exc))
        return None


async def _active_plan_for_member(
    client: httpx.AsyncClient, site_id: str, member_id: str
) -> tuple[str | None, str | None, str]:
    """Return (plan_id, plan_name, status) for the member's best pricing-plan order."""
    try:
        resp = await client.get(
            f"{_WIX_API}/pricing-plans/v2/orders",
            headers=_auth_headers(site_id=site_id),
            params={
                "buyerIds": member_id,
                "orderStatuses": ["ACTIVE", "PENDING", "PAUSED", "CANCELED", "ENDED"],
                "limit": 50,
            },
            timeout=20.0,
        )
        if resp.status_code >= 400:
            logger.warning("Wix orders list failed", status=resp.status_code, body=resp.text[:300])
            return None, None, "INACTIVE"
        orders = (resp.json() or {}).get("orders") or []
        if not orders:
            return None, None, "INACTIVE"

        def _rank(order: dict[str, Any]) -> int:
            status = str(order.get("status") or "").upper()
            return {"ACTIVE": 0, "PENDING": 1, "PAUSED": 2}.get(status, 9)

        orders_sorted = sorted(orders, key=_rank)
        best = orders_sorted[0]
        status = str(best.get("status") or "INACTIVE").upper()
        plan_id = best.get("planId") or best.get("plan_id")
        plan_name = best.get("planName") or best.get("plan_name")
        return (
            str(plan_id).strip() if plan_id else None,
            str(plan_name).strip() if plan_name else None,
            status,
        )
    except Exception as exc:
        logger.warning("Wix orders lookup error", member_id=member_id, error=str(exc))
        return None, None, "INACTIVE"


def _tier_from_plan(plan_id: str | None, plan_name: str | None, status: str) -> tuple[str, str]:
    """Map plan + status to (tier, subscription_status)."""
    if status in ("CANCELED", "CANCELLED", "EXPIRED", "INACTIVE", "ENDED"):
        return "free", "canceled"
    if status == "PAUSED":
        # Treat paused as inactive for caps until resumed.
        return "free", "canceled"
    tier = wix_plan_to_tier(plan_id, plan_name)
    return tier, ("active" if tier != "free" else "canceled")


async def fetch_wix_tier_for_email(email: str) -> tuple[str, str] | None:
    """
    Resolve the website package for ``email`` via Wix APIs.

    Returns (tier, subscription_status) or None if Wix is not configured / lookup failed
    (caller must keep the existing DB tier).
    """
    if not _wix_configured():
        return None
    email_n = (email or "").strip().lower()
    if not email_n or "@" not in email_n:
        return None

    async with httpx.AsyncClient() as client:
        site_id = await _resolve_site_id(client)
        if not site_id:
            return None
        member_id = await _find_member_id(client, site_id, email_n)
        if not member_id:
            # Member not on Wix site → free trial / no package.
            return "free", "canceled"
        plan_id, plan_name, status = await _active_plan_for_member(client, site_id, member_id)
        return _tier_from_plan(plan_id, plan_name, status)


async def apply_wix_tier_to_user(
    user_id: str,
    email: str,
    *,
    force: bool = False,
) -> dict[str, str] | None:
    """
    Refresh ``users.tier`` from Wix for this email (absolute replace).

    Returns {"tier", "subscription_status"} when a refresh ran (or cache hit with no change needed
    is skipped — returns None on skip/failure so callers keep current values).
    On successful Wix lookup, always updates DB when values differ and returns the effective tier.
    """
    email_n = (email or "").strip().lower()
    if not email_n:
        return None
    if not _wix_configured():
        return None

    now = time.monotonic()
    deadline = _refresh_cache.get(email_n, 0.0)
    if not force and now < deadline:
        return None

    resolved = await fetch_wix_tier_for_email(email_n)
    _refresh_cache[email_n] = now + _REFRESH_TTL_SEC
    if resolved is None:
        return None

    tier, subscription_status = resolved
    try:
        sb = await get_supabase()
        await sb.table("users").update(
            {"tier": tier, "subscription_status": subscription_status}
        ).eq("id", user_id).execute()
        logger.info(
            "Wix tier refresh applied",
            user_id=user_id,
            email=email_n,
            tier=tier,
            subscription_status=subscription_status,
            force=force,
        )
        return {"tier": tier, "subscription_status": subscription_status}
    except Exception as exc:
        logger.warning("Wix tier DB update failed", user_id=user_id, error=str(exc))
        return None


async def refresh_user_tier_from_wix(user: dict, *, force: bool = False) -> dict:
    """
    Best-effort refresh of ``user`` dict tier fields from Wix.

    Mutates and returns the same ``user`` mapping. Never raises; never clears tier on failure.
    """
    user_id = str(user.get("id") or "")
    email = str(user.get("email") or "")
    if not user_id or not email:
        return user
    result = await apply_wix_tier_to_user(user_id, email, force=force)
    if result:
        user["tier"] = result["tier"]
        user["subscription_status"] = result["subscription_status"]
    return user
