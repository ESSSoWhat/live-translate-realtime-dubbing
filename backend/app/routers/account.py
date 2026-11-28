"""Account deletion sync endpoints (Wix member <-> app account)."""

from __future__ import annotations

import hmac
from typing import Any

import structlog  # pylint: disable=import-error
from fastapi import APIRouter, HTTPException, Request, status  # pylint: disable=import-error
from pydantic import BaseModel, Field  # pylint: disable=import-error

from app.config import get_settings
from app.services import account_sync

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/account", tags=["account"])


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization") or request.headers.get("X-Wix-Sync-Secret") or ""
    return header[len("Bearer "):].strip() if header.startswith("Bearer ") else header.strip()


def _require_secret(request: Request, expected: str, name: str) -> None:
    expected = (expected or "").strip()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} is not configured.")
    supplied = _bearer(request)
    if not supplied:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing credentials")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


def _http_error(exc: account_sync.AccountSyncError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


class WixMemberDeletedRequest(BaseModel):
    """Sent by the Wix site's events.js when a member is deleted."""

    member_id: str = Field(min_length=36, max_length=36)


@router.post("/sync/wix-member-deleted", status_code=status.HTTP_200_OK)
async def wix_member_deleted(body: WixMemberDeletedRequest, request: Request) -> dict:
    """Wix -> app: delete the app account linked to a deleted Wix member (by wix_member_id)."""
    _require_secret(request, get_settings().lt_sync_secret, "LT_SYNC_SECRET")
    if not account_sync.sync_enabled():
        return {"status": "disabled"}
    try:
        return await account_sync.handle_wix_member_deleted(body.member_id)
    except account_sync.AccountSyncError as exc:
        logger.warning("wix-member-deleted sync failed", error=str(exc))
        raise _http_error(exc) from exc


class SupabaseDeleteHook(BaseModel):
    """Supabase Database Webhook payload for DELETE on auth.users."""

    type: str
    table: str
    schema_: str = Field(alias="schema")
    record: dict[str, Any] | None = None
    old_record: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


@router.post("/sync/supabase-user-deleted", status_code=status.HTTP_200_OK)
async def supabase_user_deleted(body: SupabaseDeleteHook, request: Request) -> dict:
    """Supabase -> Wix: an auth.users row was deleted; delete the Wix member and app rows."""
    _require_secret(request, get_settings().account_sync_webhook_secret, "ACCOUNT_SYNC_WEBHOOK_SECRET")
    if body.type.upper() != "DELETE" or body.table != "users" or body.schema_ != "auth":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Expected a DELETE on auth.users")
    old = body.old_record or {}
    uid = str(old.get("id") or "").strip()
    if not uid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "old_record.id is required")
    if not account_sync.sync_enabled():
        return {"status": "disabled"}
    email = old.get("email") if isinstance(old.get("email"), str) else None
    try:
        return await account_sync.handle_supabase_user_deleted(uid, email)
    except account_sync.AccountSyncError as exc:
        logger.warning("supabase-user-deleted sync failed", error=str(exc))
        raise _http_error(exc) from exc
