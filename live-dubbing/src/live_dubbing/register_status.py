"""Classify /auth/register responses without talking to a backend."""

from __future__ import annotations

from typing import Any

CONFIRM_EMAIL_MESSAGE = "Check your email to confirm your account, then sign in."
SIGNUP_TIMEOUT_MESSAGE = "This is taking longer than usual. Check your inbox before trying again."


def is_confirmation_message(text: str) -> bool:
    """True for the current and previous 'check your email' copy."""
    lowered = (text or "").lower()
    return "check your email" in lowered and "confirm" in lowered


def classify_register_response(
    status_code: int,
    body: Any = None,
    *,
    timed_out: bool = False,
) -> tuple[str, str, dict]:
    """Return (kind, message, payload).

    kind is one of: signed_in, needs_confirmation, timeout, error.
    """
    payload = body if isinstance(body, dict) else {}
    detail = payload.get("detail") if payload else body
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict) and "msg" in item:
                parts.append(str(item["msg"]))
            else:
                parts.append(str(item))
        detail_text = " ".join(parts)
    elif detail is None:
        detail_text = ""
    else:
        detail_text = str(detail)
    message = str(payload.get("message") or detail_text or "")

    if timed_out or status_code == 504:
        return "timeout", SIGNUP_TIMEOUT_MESSAGE, payload

    if payload.get("needs_confirmation") is True or is_confirmation_message(message):
        return (
            "needs_confirmation",
            message or CONFIRM_EMAIL_MESSAGE,
            {**payload, "needs_confirmation": True, "message": message or CONFIRM_EMAIL_MESSAGE},
        )

    if status_code in (200, 201) and (payload.get("access_token") or payload.get("refresh_token")):
        return "signed_in", "", payload

    if status_code in (200, 201, 202) and not payload.get("access_token"):
        # Direct Supabase signup or a 201 with no session.
        return (
            "needs_confirmation",
            CONFIRM_EMAIL_MESSAGE,
            {"needs_confirmation": True, "message": CONFIRM_EMAIL_MESSAGE},
        )

    return "error", message or f"HTTP {status_code}", payload
