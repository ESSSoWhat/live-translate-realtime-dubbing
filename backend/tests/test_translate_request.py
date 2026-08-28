"""TranslateRequest accepts prior_context and the older context alias."""

from app.models.requests import TranslateRequest


def test_prior_context_field() -> None:
    body = TranslateRequest.model_validate(
        {
            "text": "hello",
            "target_language": "en",
            "prior_context": "yesterday we met",
        }
    )
    assert body.prior_context == "yesterday we met"


def test_legacy_context_alias() -> None:
    body = TranslateRequest.model_validate(
        {
            "text": "hello",
            "target_language": "en",
            "context": "yesterday we met",
        }
    )
    assert body.prior_context == "yesterday we met"


def test_prior_context_defaults_empty() -> None:
    body = TranslateRequest(text="hello", target_language="en")
    assert body.prior_context == ""
