"""One-time SMS codes that prove a user owns the phone number they call from.

Uses Twilio Verify when TWILIO_VERIFY_SERVICE_SID is set. Otherwise the backend
creates a 6-digit code, keeps only its hash, and texts it from TWILIO_PHONE_NUMBER.

Pending codes, attempt counts, and send limits live in Supabase so they survive
process restarts and are shared across instances. When no database is available,
or PHONE_VERIFY_STORE=memory is set (tests and local dev), the same rules are
kept in process memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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


def _wrong(message: str = "That code isn't right.") -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "invalid_code", "message": message})


def _memory_forced() -> bool:
    return os.environ.get("PHONE_VERIFY_STORE", "").strip().lower() in {"memory", "mem", "in-memory"}


def _utc(now: datetime | float | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    # Tests for the memory store pass a monotonic float; the database store
    # only sees this if a test injects it, so treat it as seconds since epoch.
    return datetime.fromtimestamp(float(now), tz=timezone.utc)


def _raise_status(status: str) -> None:
    if status == "ok":
        return
    if status == "missing":
        raise _wrong("Ask for a code for this number first.")
    if status == "expired":
        raise _wrong("The code has expired. Ask for a new one.")
    if status == "too_many":
        raise _wrong("Too many wrong codes. Ask for a new one.")
    raise _wrong()


class MemoryStore:
    """Process-local store used in tests and when Supabase is not configured."""

    def check_send_limits(self, user_id: str, phone: str, now: float) -> None:
        for key in (f"u:{user_id}", f"p:{phone}"):
            q = _sends[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if q and now - q[-1] < RESEND_GAP_SEC:
                raise _too_many(
                    "Wait a minute before asking for another code.",
                    int(RESEND_GAP_SEC - (now - q[-1])),
                )
            if len(q) >= MAX_SENDS_PER_HOUR:
                raise _too_many("Too many codes requested. Try again later.", int(3600 - (now - q[0])))

    def record_send(self, user_id: str, phone: str, now: float) -> str:
        for key in (f"u:{user_id}", f"p:{phone}"):
            _sends[key].append(now)
        return f"{user_id}:{now}"

    def release_send(self, send_id: str, user_id: str, phone: str) -> None:
        for key in (f"u:{user_id}", f"p:{phone}"):
            q = _sends[key]
            if q:
                q.pop()

    def put_pending(self, user_id: str, phone: str, code_hash: str | None, expires: float) -> None:
        if code_hash is None:
            _pending.pop(user_id, None)
            _verify_attempts[user_id] = 0
        else:
            _pending[user_id] = _Pending(phone=phone, code_hash=code_hash, expires=expires)
            _verify_attempts.setdefault(user_id, 0)
        _last_phone[user_id] = phone

    def precheck(self, user_id: str, phone: str) -> str:
        """Read-only check, before any Twilio Verify call, that a code is pending and not locked out."""
        if _last_phone.get(user_id) != phone:
            return "missing"
        if _verify_attempts.get(user_id, 0) >= MAX_ATTEMPTS:
            return "too_many"
        return "ok"

    def attempt(self, user_id: str, phone: str, *, code_hash: str | None = None, approved: bool | None = None, now: float) -> str:
        if _last_phone.get(user_id) != phone:
            return "missing"
        if approved is not None:
            if _verify_attempts.get(user_id, 0) >= MAX_ATTEMPTS:
                return "too_many"
            if approved:
                _pending.pop(user_id, None)
                _verify_attempts.pop(user_id, None)
                _last_phone.pop(user_id, None)
                return "ok"
            _verify_attempts[user_id] = _verify_attempts.get(user_id, 0) + 1
            return "invalid"
        p = _pending.get(user_id)
        if p is None or p.phone != phone or now > p.expires:
            _pending.pop(user_id, None)
            return "expired" if _last_phone.get(user_id) == phone else "missing"
        if p.attempts >= MAX_ATTEMPTS:
            return "too_many"
        if not (code_hash and hmac.compare_digest(p.code_hash, code_hash)):
            p.attempts += 1
            return "invalid"
        _pending.pop(user_id, None)
        _verify_attempts.pop(user_id, None)
        _last_phone.pop(user_id, None)
        return "ok"


class SupabaseStore:
    """Shared store backed by phone_verification_codes / phone_verification_sends."""

    def __init__(self, db: Any):
        self.db = db

    async def reserve_send(self, user_id: str, phone: str, now: datetime | None = None) -> str:
        params: dict[str, Any] = {"p_user_id": user_id, "p_phone": phone}
        if now is not None:
            params["p_now"] = _utc(now).isoformat()
        res = await self.db.rpc("try_record_phone_verification_send", params).execute()
        payload = _rpc_payload(res)
        status = payload.get("status")
        if status == "resend_gap":
            raise _too_many("Wait a minute before asking for another code.", int(payload.get("retry_after") or RESEND_GAP_SEC))
        if status == "hourly_limit":
            raise _too_many("Too many codes requested. Try again later.", int(payload.get("retry_after") or 3600))
        if status != "ok" or not payload.get("send_id"):
            raise HTTPException(
                status_code=503,
                detail={"error": "verify_store_unavailable", "message": "Couldn't start verification. Please try again."},
            )
        return str(payload["send_id"])

    async def release_send(self, send_id: str) -> None:
        await self.db.table("phone_verification_sends").delete().eq("id", send_id).execute()

    async def put_pending(self, user_id: str, phone: str, code_hash: str | None, expires: datetime) -> None:
        row = {
            "user_id": user_id,
            "phone_e164": phone,
            "code_hash": code_hash,
            "expires_at": _utc(expires).isoformat(),
            "attempts": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.db.table("phone_verification_codes").upsert(row, on_conflict="user_id").execute()

    async def precheck(self, user_id: str, phone: str) -> str:
        """Read-only check, before any Twilio Verify call, that a code is pending and not locked out.

        apply_phone_verification_attempt stays the authority (it locks the row and
        counts the attempt); this only avoids calling Twilio for requests that RPC
        would reject anyway (no pending code for this number, or locked out).
        Expiry is left to the RPC so it is judged by the database clock.
        """
        res = await (
            self.db.table("phone_verification_codes")
            .select("phone_e164, attempts")
            .eq("user_id", user_id)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        rec = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
        if not rec or rec.get("phone_e164") != phone:
            return "missing"
        if int(rec.get("attempts") or 0) >= MAX_ATTEMPTS:
            return "too_many"
        return "ok"

    async def attempt(
        self,
        user_id: str,
        phone: str,
        *,
        code_hash: str | None = None,
        approved: bool | None = None,
    ) -> str:
        res = await self.db.rpc(
            "apply_phone_verification_attempt",
            {
                "p_user_id": user_id,
                "p_phone": phone,
                "p_code_hash": code_hash,
                "p_approved": approved,
            },
        ).execute()
        status = _rpc_payload(res)
        if isinstance(status, dict):
            status = status.get("status") or status.get("apply_phone_verification_attempt")
        return str(status or "")


def _rpc_payload(res: Any) -> Any:
    data = getattr(res, "data", res)
    if isinstance(data, list):
        data = data[0] if data else {}
    return data


_memory = MemoryStore()


async def _resolve_store(db: Any | None, store: Any | None) -> Any:
    if store is not None:
        return store
    if db is not None and not _memory_forced():
        return SupabaseStore(db)
    return _memory


def _twilio(cfg: Settings):
    from twilio.rest import Client

    return Client(cfg.twilio_account_sid, cfg.twilio_auth_token)


async def send_code(
    user_id: str,
    phone: str,
    cfg: Settings,
    now: float | datetime | None = None,
    db: Any | None = None,
    store: Any | None = None,
) -> None:
    store = await _resolve_store(db, store)
    client = _twilio(cfg)
    if isinstance(store, SupabaseStore):
        when = _utc(now)
        send_id = await store.reserve_send(user_id, phone, when)
        try:
            if cfg.twilio_verify_service_sid:
                svc = client.verify.v2.services(cfg.twilio_verify_service_sid)
                await asyncio.to_thread(svc.verifications.create, to=phone, channel="sms")
                code_hash = None
            else:
                code = f"{secrets.randbelow(1_000_000):06d}"
                await asyncio.to_thread(
                    client.messages.create,
                    to=phone,
                    from_=cfg.twilio_phone_number,
                    body=f"Your LiveTranslate code is {code}. It expires in 10 minutes.",
                )
                code_hash = _hash(code, phone)
            await store.put_pending(user_id, phone, code_hash, when + timedelta(seconds=CODE_TTL_SEC))
        except Exception:
            await store.release_send(send_id)
            raise
        return

    when_f = time.monotonic() if now is None else float(now)
    store.check_send_limits(user_id, phone, when_f)
    send_id = None
    try:
        if cfg.twilio_verify_service_sid:
            svc = client.verify.v2.services(cfg.twilio_verify_service_sid)
            await asyncio.to_thread(svc.verifications.create, to=phone, channel="sms")
            store.put_pending(user_id, phone, None, when_f + CODE_TTL_SEC)
        else:
            code = f"{secrets.randbelow(1_000_000):06d}"
            await asyncio.to_thread(
                client.messages.create,
                to=phone,
                from_=cfg.twilio_phone_number,
                body=f"Your LiveTranslate code is {code}. It expires in 10 minutes.",
            )
            store.put_pending(user_id, phone, _hash(code, phone), when_f + CODE_TTL_SEC)
        send_id = store.record_send(user_id, phone, when_f)
    except Exception:
        if send_id is not None:
            store.release_send(send_id, user_id, phone)
        raise


async def check_code(
    user_id: str,
    phone: str,
    code: str,
    cfg: Settings,
    now: float | datetime | None = None,
    db: Any | None = None,
    store: Any | None = None,
) -> None:
    """Raise unless ``code`` is the latest code sent to ``phone`` for this user."""
    store = await _resolve_store(db, store)
    code = (code or "").strip()
    if isinstance(store, SupabaseStore):
        if cfg.twilio_verify_service_sid:
            # Don't spend a Twilio Verify call on a request the store would reject.
            _raise_status(await store.precheck(user_id, phone))
            svc = _twilio(cfg).verify.v2.services(cfg.twilio_verify_service_sid)
            try:
                check = await asyncio.to_thread(svc.verification_checks.create, to=phone, code=code)
                ok = getattr(check, "status", "") == "approved"
            except Exception:  # Twilio returns 404 once a verification expired or was used
                ok = False
            status = await store.attempt(user_id, phone, approved=ok)
        else:
            digest = _hash(code, phone) if code else ""
            status = await store.attempt(user_id, phone, code_hash=digest)
        _raise_status(status)
        return

    when_f = time.monotonic() if now is None else float(now)
    if cfg.twilio_verify_service_sid:
        _raise_status(store.precheck(user_id, phone))
        svc = _twilio(cfg).verify.v2.services(cfg.twilio_verify_service_sid)
        try:
            check = await asyncio.to_thread(svc.verification_checks.create, to=phone, code=code)
            ok = getattr(check, "status", "") == "approved"
        except Exception:  # Twilio returns 404 once a verification expired or was used
            ok = False
        status = store.attempt(user_id, phone, approved=ok, now=when_f)
    else:
        digest = _hash(code, phone) if (code and code.isdigit()) else ""
        status = store.attempt(user_id, phone, code_hash=digest, now=when_f)
    _raise_status(status)


def reset() -> None:
    """Clear all in-memory state (tests)."""
    _pending.clear()
    _verify_attempts.clear()
    _sends.clear()
    _last_phone.clear()
