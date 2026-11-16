"""One-time SMS codes that prove a user owns the phone number they call from.

Uses Twilio Verify when TWILIO_VERIFY_SERVICE_SID is set. Otherwise the backend
creates a 6-digit code, keeps only its hash, and texts it from TWILIO_PHONE_NUMBER.
Pending codes and send limits are in memory (per process); a restart just means
"send a new code".
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException

from app.config import Settings

CODE_TTL_SEC = 600
MAX_ATTEMPTS = 5
RESEND_GAP_SEC = 60
MAX_SENDS_PER_HOUR = 5


@dataclass
class _Pending:
    phone: str
    code_hash: str
    expires: float
    attempts: int = 0


_pending: dict[str, _Pending] = {}  # user_id -> local-code verification
_verify_attempts: dict[str, int] = {}  # user_id -> wrong codes (Twilio Verify path)
_sends: dict[str, deque[float]] = defaultdict(deque)  # "u:<id>" / "p:<phone>" -> send times
_last_phone: dict[str, str] = {}  # user_id -> number the latest code went to


def _hash(code: str, phone: str) -> str:
    return hashlib.sha256(f"{phone}:{code}".encode()).hexdigest()


def _too_many(message: str, retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"error": "verify_rate_limited", "message": message},
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _check_send_limits(user_id: str, phone: str, now: float) -> None:
    for key in (f"u:{user_id}", f"p:{phone}"):
        q = _sends[key]
        while q and now - q[0] > 3600:
            q.popleft()
        if q and now - q[-1] < RESEND_GAP_SEC:
            raise _too_many("Wait a minute before asking for another code.", int(RESEND_GAP_SEC - (now - q[-1])))
        if len(q) >= MAX_SENDS_PER_HOUR:
            raise _too_many("Too many codes requested. Try again later.", int(3600 - (now - q[0])))


def _twilio(cfg: Settings):
    from twilio.rest import Client

    return Client(cfg.twilio_account_sid, cfg.twilio_auth_token)


async def send_code(user_id: str, phone: str, cfg: Settings, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    _check_send_limits(user_id, phone, now)
    client = _twilio(cfg)
    if cfg.twilio_verify_service_sid:
        svc = client.verify.v2.services(cfg.twilio_verify_service_sid)
        await asyncio.to_thread(svc.verifications.create, to=phone, channel="sms")
        _pending.pop(user_id, None)
        _verify_attempts[user_id] = 0
    else:
        code = f"{secrets.randbelow(1_000_000):06d}"
        await asyncio.to_thread(
            client.messages.create,
            to=phone,
            from_=cfg.twilio_phone_number,
            body=f"Your LiveTranslate code is {code}. It expires in 10 minutes.",
        )
        _pending[user_id] = _Pending(phone=phone, code_hash=_hash(code, phone), expires=now + CODE_TTL_SEC)
    for key in (f"u:{user_id}", f"p:{phone}"):
        _sends[key].append(now)
    _verify_attempts.setdefault(user_id, 0)
    _last_phone[user_id] = phone


def _wrong(message: str = "That code isn't right.") -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "invalid_code", "message": message})


async def check_code(user_id: str, phone: str, code: str, cfg: Settings, now: float | None = None) -> None:
    """Raise unless ``code`` is the latest code sent to ``phone`` for this user."""
    now = time.monotonic() if now is None else now
    code = (code or "").strip()
    if _last_phone.get(user_id) != phone:
        raise _wrong("Ask for a code for this number first.")
    if cfg.twilio_verify_service_sid:
        if _verify_attempts.get(user_id, 0) >= MAX_ATTEMPTS:
            raise _wrong("Too many wrong codes. Ask for a new one.")
        svc = _twilio(cfg).verify.v2.services(cfg.twilio_verify_service_sid)
        try:
            check = await asyncio.to_thread(svc.verification_checks.create, to=phone, code=code)
            ok = getattr(check, "status", "") == "approved"
        except Exception:  # Twilio returns 404 once a verification expired or was used
            ok = False
        if not ok:
            _verify_attempts[user_id] = _verify_attempts.get(user_id, 0) + 1
            raise _wrong()
    else:
        p = _pending.get(user_id)
        if p is None or p.phone != phone or now > p.expires:
            _pending.pop(user_id, None)
            raise _wrong("The code has expired. Ask for a new one.")
        if p.attempts >= MAX_ATTEMPTS:
            raise _wrong("Too many wrong codes. Ask for a new one.")
        if not (code.isdigit() and hmac.compare_digest(p.code_hash, _hash(code, phone))):
            p.attempts += 1
            raise _wrong()
    _pending.pop(user_id, None)
    _verify_attempts.pop(user_id, None)
    _last_phone.pop(user_id, None)


def reset() -> None:
    """Clear all in-memory state (tests)."""
    _pending.clear()
    _verify_attempts.clear()
    _sends.clear()
    _last_phone.clear()
