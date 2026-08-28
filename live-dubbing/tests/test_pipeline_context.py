"""Rolling source context and translate JSON field name."""

from __future__ import annotations

import asyncio

from live_dubbing.processing.pipeline import (
    PipelineConfig,
    ProcessingPipeline,
    append_prior_context,
)
from live_dubbing.services.backend_service import BackendProxyService


def test_append_prior_context_joins_and_trims() -> None:
    assert append_prior_context("", "  hello  ") == "hello"
    assert append_prior_context("hello", "world") == "hello world"
    assert append_prior_context("keep", "   ") == "keep"


def test_append_prior_context_caps_length() -> None:
    old = "a" * 1400
    new = "b" * 200
    out = append_prior_context(old, new, max_chars=1500)
    assert len(out) <= 1500
    assert out.endswith("b" * 200)


def test_remember_source_uses_rolling_window() -> None:
    pipe = object.__new__(ProcessingPipeline)
    pipe._config = PipelineConfig(preserve_context=True)
    pipe._source_context = ""
    pipe._remember_source("first line")
    pipe._remember_source("second line")
    assert pipe._source_context == "first line second line"


def test_remember_source_respects_preserve_context_off() -> None:
    pipe = object.__new__(ProcessingPipeline)
    pipe._config = PipelineConfig(preserve_context=False)
    pipe._source_context = ""
    pipe._remember_source("ignored")
    assert pipe._source_context == ""


def test_backend_translate_sends_prior_context_field() -> None:
    async def _run() -> None:
        svc = BackendProxyService("http://example.invalid", "tok", "ref")
        captured: dict[str, object] = {}

        class _Resp:
            def json(self) -> dict[str, str]:
                return {"translated_text": "hi"}

        async def _fake_request(method: str, path: str, **kwargs: object) -> _Resp:
            captured.update(kwargs)
            return _Resp()

        svc._request = _fake_request  # type: ignore[method-assign]
        await svc.translate_text("new line", "en", "ja", "prior spoken")
        payload = captured["json"]
        assert isinstance(payload, dict)
        assert payload["prior_context"] == "prior spoken"
        assert "context" not in payload
        await svc._client.aclose()

    asyncio.run(_run())
