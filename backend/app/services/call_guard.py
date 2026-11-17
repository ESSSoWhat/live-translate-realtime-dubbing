"""Abuse guards for translated phone calls: plan, number checks, rate limits, Twilio signatures."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import Settings

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")  # 8+ digits keeps short codes out

# Premium-rate / pay-per-call ranges (E.164 prefixes). Not exhaustive; pair with
# Twilio Geo Permissions and TWILIO_ALLOWED_PREFIXES for real coverage.
BLOCKED_PREFIXES = (
    "+1900", "+1976",  # North America premium / pay-per-call
    "+4470", "+44871", "+44872", "+44873", "+4490", "+4491", "+4498",  # UK premium / personal numbers
    "+6119",  # Australia 19xx premium
    "+3389",  # France premium (08 9x)
    "+49900", "+49137",  # Germany premium / mass-calling
    "+39899",  # Italy premium
    "+34803", "+34806", "+34807", "+3490",  # Spain premium
    "+6419", "+64900",  # New Zealand premium
    "+881", "+882", "+883", "+979",  # satellite / international networks / premium
)

# Stripe/Wix statuses that actually grant a paid entitlement. A paid tier with
# incomplete, paused, or empty status must not be able to start billable calls.
PAID_STATUS_ALLOWED = {"active", "trialing"}


def normalize_phone(raw: str) -> str:
    """Strip spaces, dashes, dots and brackets; the result must be E.164."""
    return re.sub(r"[\s().-]", "", raw or "")


def check_destination(number: str, cfg: Settings, field: str) -> str:
    n = normalize_phone(number)
    if not _E164.match(n):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_phone", "field": field,
                    "message": "Use international format, e.g. +61412345678."},
        )
    if n.startswith(BLOCKED_PREFIXES):
        raise HTTPException(
            status_code=422,
            detail={"error": "blocked_destination", "field": field,
                    "message": "Premium-rate and special numbers can't be called."},
        )
    allowed = [p.strip() for p in cfg.twilio_allowed_prefixes.split(",") if p.strip()]
    if allowed and not n.startswith(tuple(allowed)):
        raise HTTPException(
            status_code=422,
            detail={"error": "destination_not_allowed", "field": field,
                    "message": "Calls to this country aren't enabled."},
        )
    return n


def require_call_plan(user: dict, cfg: Settings) -> None:
    tiers = {t.strip() for t in cfg.twilio_call_tiers.split(",") if t.strip()}
    tier = str(user.get("tier") or "free")
    status = str(user.get("subscription_status") or "")
    if tier not in tiers or status not in PAID_STATUS_ALLOWED:
        raise HTTPException(
            status_code=403,
            detail={"error": "plan_required", "tier": tier,
                    "message": "Translated phone calls need a paid plan (Hobby or Pro)."},
        )


class CallRateLimiter:
    """Calls started per user in the last hour (in-memory, per backend process)."""

    def __init__(self) -> None:
        self._starts: dict[str, deque[float]] = defaultdict(deque)

    def check_and_record(self, user_id: str, per_hour: int, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        q = self._starts[user_id]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= per_hour:
            retry = int(3600 - (now - q[0])) + 1
            raise HTTPException(
                status_code=429,
                detail={"error": "call_rate_limited", "limit_per_hour": per_hour,
                        "message": "Too many calls this hour. Try again later."},
                headers={"Retry-After": str(retry)},
            )
        q.append(now)


rate_limiter = CallRateLimiter()


def public_request_url(request: Request, cfg: Settings) -> str:
    """The URL Twilio signed: configured public base + path + raw query.

    Behind Railway/Vercel proxies ``request.url`` has the internal scheme/host,
    so the signature is checked against LIVE_TRANSLATE_PUBLIC_URL instead.
    """
    base = (cfg.live_translate_public_url or "").rstrip("/")
    if not base:
        base = f"{request.url.scheme}://{request.url.netloc}"
    url = base + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    return url


async def verify_twilio_request(request: Request, cfg: Settings) -> None:
    """403 unless X-Twilio-Signature is valid. Fails closed in production without a token."""
    token = cfg.twilio_auth_token
    if not token:
        if cfg.is_production:
            raise HTTPException(status_code=403, detail="Twilio signature cannot be verified")
        return  # local development without Twilio
    signature = request.headers.get("X-Twilio-Signature", "")
    params: dict[str, str] = {}
    if request.method == "POST":
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
    from twilio.request_validator import RequestValidator

    if not signature or not RequestValidator(token).validate(public_request_url(request, cfg), params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
