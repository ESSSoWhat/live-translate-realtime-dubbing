"""Map Wix Pricing Plan ids/names to backend tiers (free/starter/pro/early_adopters)."""

from __future__ import annotations

# Stable plan-ID → tier (Wix plan GUIDs; preferred — names can change).
WIX_PLAN_ID_TO_TIER: dict[str, str] = {
    "146fe70b-7ba2-4ec4-b9cc-72c77c645aac": "pro",
    "fe160051-2f9a-4a28-929a-053ece47dcc7": "starter",
    "954bf0a7-cb41-4726-ace1-c61c7a75425c": "early_adopters",
    "5f9d4418-240c-4dcf-8748-c006011eff2f": "free",
}

# Exact plan name (lowercased) → tier.
WIX_PLAN_NAME_TO_TIER: dict[str, str] = {
    "early adopters life time access": "early_adopters",
    "early adopters lifetime access": "early_adopters",
    "monthly language unlocked - pro tier": "pro",
    "monthly language unlocked - hobby tier": "starter",
    "free trial": "free",
}

# Keyword substrings (checked in order) → tier.
WIX_KEYWORD_TO_TIER: tuple[tuple[str, str], ...] = (
    ("early adopters", "early_adopters"),
    ("lifetime", "early_adopters"),
    ("pro tier", "pro"),
    ("pro", "pro"),
    ("hobby", "starter"),
    ("starter", "starter"),
    ("free trial", "free"),
)


_FREE_TRIAL_PLAN_IDS = {"5f9d4418-240c-4dcf-8748-c006011eff2f"}
_STOPPED = {"CANCELED", "CANCELLED", "EXPIRED", "INACTIVE", "ENDED", "PAUSED"}


def is_free_trial_plan(plan_id: str | None, plan_name: str | None) -> bool:
    """The Free Trial pricing plan, not a bare account with no plan."""
    if (plan_id or "").strip() in _FREE_TRIAL_PLAN_IDS:
        return True
    return "free trial" in (plan_name or "").strip().lower()


def subscription_for_wix_plan(plan_id: str | None, plan_name: str | None, status: str) -> tuple[str, str]:
    """Map a Wix order to (tier, subscription_status).

    An active Free Trial is ``trialing``. No plan, or a stopped plan, is ``canceled``.
    A bare signup is not given an offer here.
    """
    state = (status or "").strip().upper()
    if state in _STOPPED:
        return "free", "canceled"
    tier = wix_plan_to_tier(plan_id, plan_name)
    if tier == "free":
        if is_free_trial_plan(plan_id, plan_name):
            return "free", "trialing"
        return "free", "canceled"
    return tier, "active"


def wix_plan_to_tier(plan_id: str | None, plan_name: str | None) -> str:
    """Map a Wix plan id/name to a backend tier (free/starter/pro/early_adopters)."""
    if plan_id and plan_id.strip() in WIX_PLAN_ID_TO_TIER:
        return WIX_PLAN_ID_TO_TIER[plan_id.strip()]

    name = (plan_name or "").strip().lower()
    if not name:
        return "free"

    if name in WIX_PLAN_NAME_TO_TIER:
        return WIX_PLAN_NAME_TO_TIER[name]

    for keyword, tier in WIX_KEYWORD_TO_TIER:
        if keyword in name:
            return tier
    return "free"


def active_wix_tier_mapping() -> dict:
    """Return a summary of the active Wix plan->tier mapping (for startup logging)."""
    return {
        "plan_ids": dict(WIX_PLAN_ID_TO_TIER),
        "plan_names": dict(WIX_PLAN_NAME_TO_TIER),
        "keywords": [f"{k}->{t}" for k, t in WIX_KEYWORD_TO_TIER],
    }
