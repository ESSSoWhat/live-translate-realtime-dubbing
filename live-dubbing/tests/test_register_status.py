"""Synthetic tests for sign-up response classification. No backend calls."""

from live_dubbing.register_status import (
    CONFIRM_EMAIL_MESSAGE,
    SIGNUP_TIMEOUT_MESSAGE,
    classify_register_response,
    is_confirmation_message,
)


def test_202_with_needs_confirmation_is_success() -> None:
    kind, message, payload = classify_register_response(
        202,
        {
            "needs_confirmation": True,
            "message": CONFIRM_EMAIL_MESSAGE,
            "email": "a@b.c",
        },
    )
    assert kind == "needs_confirmation"
    assert "Check your email to confirm your account" in message
    assert payload["needs_confirmation"] is True


def test_old_400_check_email_is_treated_as_success() -> None:
    kind, message, _payload = classify_register_response(
        400,
        {"detail": "Please check your email to confirm your account, then sign in."},
    )
    assert kind == "needs_confirmation"
    assert is_confirmation_message(message)


def test_201_with_tokens_is_signed_in() -> None:
    kind, _message, payload = classify_register_response(
        201,
        {"access_token": "at", "refresh_token": "rt", "email": "a@b.c"},
    )
    assert kind == "signed_in"
    assert payload["access_token"] == "at"


def test_504_and_timeout_ask_the_user_to_check_inbox() -> None:
    kind, message, _payload = classify_register_response(504, {"detail": "gateway"})
    assert kind == "timeout"
    assert message == SIGNUP_TIMEOUT_MESSAGE

    kind, message, _payload = classify_register_response(0, {}, timed_out=True)
    assert kind == "timeout"
    assert "Check your inbox before trying again" in message


def test_plain_auth_failure_stays_an_error() -> None:
    kind, message, _payload = classify_register_response(400, {"detail": "Authentication failed"})
    assert kind == "error"
    assert message == "Authentication failed"
