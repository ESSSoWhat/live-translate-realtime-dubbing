"""Two-way account deletion sync: Wix member <-> LiveTranslate app account.

Rules
-----
* Every deletion is matched **exactly**: by the stored ``users.wix_member_id`` (a Wix GUID),
  by ``users.supabase_uid``, or by a case-insensitive *equal* login email. Never ``LIKE``,
  never a partial or "close" match, and an ambiguous Wix email match deletes nothing.
* Idempotent: a missing Wix member, Supabase user or ``users`` row counts as already
  deleted. Each side's deletion fires an event that comes back here; that second pass
  finds nothing left to delete. A per-user in-flight guard also stops the echo from
  racing the first pass.
* Every attempt is written to ``account_deletion_audit`` (email stored as a SHA-256 hash).
* Emails listed in ``ACCOUNT_SYNC_PROTECTED_EMAILS`` are never deleted by sync.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.services import wix_plans
from app.services.supabase_client import create_auth_client

logger = structlog.get_logger(__name__)

DELETED = "deleted"
NOT_FOUND = "not_found"
SKIPPED = "skipped"

ORIGIN_WIX = "wix"
ORIGIN_SUPABASE = "supabase"
ORIGIN_APP = "app"

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Users whose deletion is being processed in this process (stops event echoes racing us).
_in_flight: set[str] = set()


class AccountSyncError(Exception):
    """A deletion step failed in a way the caller should retry or report."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class WixUnavailableError(AccountSyncError):
    """Wix could not be reached, is not configured, or rejected the call."""


class AmbiguousMatchError(AccountSyncError):
    """More than one record matched; nothing is deleted."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 409)


class ProtectedAccountError(AccountSyncError):
    """The account is on the protected list."""

    def __init__(self) -> None:
        super().__init__("This account is protected from automatic deletion.", 403)


# ── helpers ───────────────────────────────────────────────────────────────────


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def email_digest(email: str | None) -> str | None:
    value = normalize_email(email)
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def is_wix_guid(value: str | None) -> bool:
    return bool(value and _GUID_RE.match(value.strip()))


def sync_enabled() -> bool:
    return bool(get_settings().account_delete_sync_enabled)


def _protected_emails() -> set[str]:
    raw = get_settings().account_sync_protected_emails or ""
    return {normalize_email(part) for part in raw.split(",") if part.strip()}


def is_protected(email: str | None) -> bool:
    value = normalize_email(email)
    return bool(value) and value in _protected_emails()


def _first(data: Any) -> dict | None:
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def _all(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return [data] if isinstance(data, dict) else []


# ── Wix Members REST ──────────────────────────────────────────────────────────


async def _wix_request(method: str, path: str, *, json_body: dict | None = None) -> tuple[int, dict]:
    """Call the Wix REST API with the configured key. Returns (status, json)."""
    if not wix_plans._wix_configured():  # pylint: disable=protected-access
        raise WixUnavailableError("Wix API is not configured (WIX_API_KEY / WIX_ACCOUNT_ID).", 503)
    try:
        async with httpx.AsyncClient() as client:
            site_id = await wix_plans._resolve_site_id(client)  # pylint: disable=protected-access
            if not site_id:
                raise WixUnavailableError("Could not resolve the Wix site id.", 503)
            resp = await client.request(
                method,
                f"{wix_plans._WIX_API}{path}",  # pylint: disable=protected-access
                headers=wix_plans._auth_headers(site_id=site_id),  # pylint: disable=protected-access
                json=json_body,
                timeout=20.0,
            )
    except WixUnavailableError:
        raise
    except Exception as exc:
        raise WixUnavailableError(f"Wix request failed: {type(exc).__name__}") from exc
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {}


async def find_wix_member_id_by_email(email: str) -> str | None:
    """Return the Wix member id whose login email *equals* ``email`` (exactly one), else None."""
    wanted = normalize_email(email)
    if not wanted or "@" not in wanted:
        return None
    status_code, data = await _wix_request(
        "POST",
        "/members/v1/members/query",
        json_body={"query": {"filter": {"loginEmail": wanted}, "paging": {"limit": 5}}, "fieldsets": ["FULL"]},
    )
    if status_code >= 400:
        raise WixUnavailableError(f"Wix member lookup failed ({status_code}).")
    exact = [
        m
        for m in (data.get("members") or [])
        if isinstance(m, dict) and normalize_email(m.get("loginEmail")) == wanted and is_wix_guid(m.get("id"))
    ]
    if len(exact) > 1:
        raise AmbiguousMatchError("More than one Wix member matches this email; nothing was deleted.")
    return str(exact[0]["id"]) if exact else None


async def delete_wix_member(member_id: str) -> str:
    """Delete a Wix member by id. 404 means already deleted."""
    if not is_wix_guid(member_id):
        raise AccountSyncError("Invalid Wix member id.", 422)
    status_code, _ = await _wix_request("DELETE", f"/members/v1/members/{member_id.strip()}")
    if status_code == 404:
        return NOT_FOUND
    if status_code >= 400:
        raise WixUnavailableError(f"Wix member delete failed ({status_code}).")
    return DELETED


# ── Supabase / DB ─────────────────────────────────────────────────────────────


async def _select_users(sb: Any, column: str, value: str) -> list[dict]:
    result = await sb.table("users").select("*").eq(column, value).limit(2).execute()
    return _all(getattr(result, "data", None))


async def _auth_user_exists(sb: Any, uid: str) -> bool:
    try:
        resp = await sb.auth.admin.get_user_by_id(uid)
    except Exception as exc:
        text = f"{getattr(exc, 'code', '')} {getattr(exc, 'status', '')} {exc}".lower()
        return not ("not found" in text or "user_not_found" in text or "404" in text)
    return bool(resp is not None and getattr(resp, "user", None) is not None)


async def _delete_auth_user(sb: Any, uid: str | None) -> str:
    if not uid:
        return SKIPPED
    try:
        await sb.auth.admin.delete_user(uid)
    except Exception as exc:
        text = f"{getattr(exc, 'code', '')} {getattr(exc, 'status', '')} {exc}".lower()
        if "not found" in text or "user_not_found" in text or "404" in text:
            return NOT_FOUND
        raise AccountSyncError(f"Could not delete the Supabase auth user: {type(exc).__name__}") from exc
    return DELETED


async def _delete_app_rows(sb: Any, row: dict) -> str:
    """Delete the users row (cascades usage, voices, phone verification) and desktop handoffs."""
    api_key = row.get("api_key")
    try:
        if api_key:
            await sb.table("desktop_handoffs").delete().eq("api_key", api_key).execute()
        result = await sb.table("users").delete().eq("id", row["id"]).execute()
    except Exception as exc:
        raise AccountSyncError(f"Could not delete the app account rows: {type(exc).__name__}") from exc
    return DELETED if _all(getattr(result, "data", None)) else NOT_FOUND


async def link_wix_member_id(sb: Any, user_id: Any, wix_member_id: str | None) -> bool:
    """Store the Wix member id on a users row. Never overwrites a different id; ignores bad ids."""
    candidate = (wix_member_id or "").strip().lower()
    if not is_wix_guid(candidate):
        return False
    try:
        taken = await sb.table("users").select("id").eq("wix_member_id", candidate).limit(1).execute()
        owner = _first(getattr(taken, "data", None))
        if owner:
            return str(owner.get("id")) == str(user_id)
        current = await sb.table("users").select("wix_member_id").eq("id", user_id).limit(1).execute()
        existing = (_first(getattr(current, "data", None)) or {}).get("wix_member_id")
        if existing and str(existing).lower() != candidate:
            logger.warning("Refusing to overwrite wix_member_id", user_id=str(user_id))
            return False
        await sb.table("users").update({"wix_member_id": candidate}).eq("id", user_id).execute()
        return True
    except Exception as exc:  # column missing before the migration, or a race: never block sign-in
        logger.warning("Could not link wix_member_id", user_id=str(user_id), error=type(exc).__name__)
        return False


async def _audit(
    sb: Any,
    *,
    origin: str,
    outcome: str,
    row: dict | None,
    supabase_uid: str | None,
    wix_member_id: str | None,
    email: str | None,
    steps: dict[str, str],
    error: str | None = None,
) -> None:
    record = {
        "origin": origin,
        "outcome": outcome,
        "user_id": str(row["id"]) if row and row.get("id") else None,
        "supabase_uid": supabase_uid,
        "wix_member_id": wix_member_id,
        "email_sha256": email_digest(email),
        "steps": steps,
        "error": error,
    }
    logger.info("account_deletion_sync", **{k: v for k, v in record.items() if k != "steps"}, steps=steps)
    try:
        await sb.table("account_deletion_audit").insert(record).execute()
    except Exception as exc:
        logger.warning("account_deletion_audit insert failed", error=type(exc).__name__)


def _claim(user_id: Any) -> bool:
    key = str(user_id)
    if key in _in_flight:
        return False
    _in_flight.add(key)
    return True


def _release(user_id: Any) -> None:
    _in_flight.discard(str(user_id))


# ── Entry points ──────────────────────────────────────────────────────────────


async def handle_wix_member_deleted(member_id: str) -> dict:
    """A Wix member was deleted: delete the linked app account. Matches ``wix_member_id`` only."""
    if not is_wix_guid(member_id):
        raise AccountSyncError("Invalid Wix member id.", 422)
    member_id = member_id.strip().lower()
    sb = await create_auth_client()
    rows = await _select_users(sb, "wix_member_id", member_id)
    steps: dict[str, str] = {"wix": NOT_FOUND}
    if not rows:
        await _audit(sb, origin=ORIGIN_WIX, outcome="no_match", row=None, supabase_uid=None,
                     wix_member_id=member_id, email=None, steps=steps)
        return {"status": "no_match"}
    row = rows[0]
    if is_protected(row.get("email")):
        steps["app"] = SKIPPED
        await _audit(sb, origin=ORIGIN_WIX, outcome="protected", row=row, supabase_uid=row.get("supabase_uid"),
                     wix_member_id=member_id, email=row.get("email"), steps=steps)
        return {"status": "protected"}
    if not _claim(row["id"]):
        return {"status": "in_progress"}
    try:
        steps["auth"] = await _delete_auth_user(sb, row.get("supabase_uid"))
        steps["app"] = await _delete_app_rows(sb, row)
    except AccountSyncError as exc:
        await _audit(sb, origin=ORIGIN_WIX, outcome="error", row=row, supabase_uid=row.get("supabase_uid"),
                     wix_member_id=member_id, email=row.get("email"), steps=steps, error=str(exc))
        raise
    finally:
        _release(row["id"])
    await _audit(sb, origin=ORIGIN_WIX, outcome="deleted", row=row, supabase_uid=row.get("supabase_uid"),
                 wix_member_id=member_id, email=row.get("email"), steps=steps)
    return {"status": "deleted", "steps": steps}


async def _row_for_deleted_auth_user(sb: Any, uid: str, email: str | None) -> dict | None:
    rows = await _select_users(sb, "supabase_uid", uid)
    if rows:
        return rows[0]
    wanted = normalize_email(email)
    if not wanted:
        return None
    # Exact email only. Take the row only if it is not owned by a different live auth user.
    for candidate in dict.fromkeys([wanted, (email or "").strip()]):
        if not candidate:
            continue
        found = await _select_users(sb, "email", candidate)
        if not found:
            continue
        owner = found[0].get("supabase_uid")
        if owner and str(owner) != uid and await _auth_user_exists(sb, str(owner)):
            return None
        return found[0]
    return None


async def handle_supabase_user_deleted(uid: str, email: str | None) -> dict:
    """An auth.users row was deleted: delete the Wix member and the app rows."""
    uid = (uid or "").strip()
    if not uid:
        raise AccountSyncError("Missing user id.", 422)
    sb = await create_auth_client()
    row = await _row_for_deleted_auth_user(sb, uid, email)
    target_email = (row or {}).get("email") or email
    steps: dict[str, str] = {"auth": NOT_FOUND}
    if is_protected(target_email):
        steps["wix"] = SKIPPED
        await _audit(sb, origin=ORIGIN_SUPABASE, outcome="protected", row=row, supabase_uid=uid,
                     wix_member_id=(row or {}).get("wix_member_id"), email=target_email, steps=steps)
        return {"status": "protected"}
    claim_id = row["id"] if row else f"uid:{uid}"
    if not _claim(claim_id):
        return {"status": "in_progress"}
    wix_id = (row or {}).get("wix_member_id")
    try:
        if not wix_id and target_email:
            wix_id = await find_wix_member_id_by_email(target_email)
        if wix_id:
            steps["wix"] = await delete_wix_member(wix_id)
        else:
            steps["wix"] = NOT_FOUND
        if row:
            steps["app"] = await _delete_app_rows(sb, row)
    except AccountSyncError as exc:
        await _audit(sb, origin=ORIGIN_SUPABASE, outcome="error", row=row, supabase_uid=uid,
                     wix_member_id=wix_id, email=target_email, steps=steps, error=str(exc))
        raise
    finally:
        _release(claim_id)
    outcome = "deleted" if (steps.get("wix") == DELETED or steps.get("app") == DELETED) else "no_match"
    await _audit(sb, origin=ORIGIN_SUPABASE, outcome=outcome, row=row, supabase_uid=uid,
                 wix_member_id=wix_id, email=target_email, steps=steps)
    return {"status": outcome, "steps": steps}


async def delete_own_account(row: dict) -> dict:
    """The signed-in app user deletes their account (and, when sync is on, the Wix member)."""
    sb = await create_auth_client()
    if is_protected(row.get("email")):
        raise ProtectedAccountError()
    if not _claim(row["id"]):
        return {"status": "in_progress"}
    steps: dict[str, str] = {}
    wix_id = row.get("wix_member_id")
    try:
        if sync_enabled():
            if not wix_id and row.get("email"):
                wix_id = await find_wix_member_id_by_email(row["email"])
            steps["wix"] = await delete_wix_member(wix_id) if wix_id else NOT_FOUND
        else:
            steps["wix"] = SKIPPED
        steps["auth"] = await _delete_auth_user(sb, row.get("supabase_uid"))
        steps["app"] = await _delete_app_rows(sb, row)
    except AccountSyncError as exc:
        await _audit(sb, origin=ORIGIN_APP, outcome="error", row=row, supabase_uid=row.get("supabase_uid"),
                     wix_member_id=wix_id, email=row.get("email"), steps=steps, error=str(exc))
        raise
    finally:
        _release(row["id"])
    await _audit(sb, origin=ORIGIN_APP, outcome="deleted", row=row, supabase_uid=row.get("supabase_uid"),
                 wix_member_id=wix_id, email=row.get("email"), steps=steps)
    return {"status": "deleted", "steps": steps}
