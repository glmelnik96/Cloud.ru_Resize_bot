"""Model fallback chain in CloudRuClient.call.

A model can be in the Cloud.ru catalog yet unavailable for this project (403),
removed (404), or fail server-side (>=500) / rate-limited (429) / unreachable
(network). Instead of failing the whole /new run, ``call`` walks the primary
model's permitted fallback chain (``_FALLBACKS``). These tests pin: which
errors trigger fallback, which propagate, that vision never falls back to a
text-only model, and that null content is a soft failure.
"""
from __future__ import annotations

import httpx
import pytest
from openai import (
    APIConnectionError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from llm.cloudru import CloudRuClient, ModelCall, ModelName


def _status_err(cls, status: int):
    req = httpx.Request("POST", "https://fm.example/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return cls("upstream said no", response=resp, body=None)


def _conn_err():
    req = httpx.Request("POST", "https://fm.example/v1/chat/completions")
    return APIConnectionError(request=req)


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)
        self.finish_reason = "stop"


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def _client_with(monkeypatch, script):
    """Build a CloudRuClient whose completions.create replays `script` (a list
    keyed by model.value): each entry is an Exception to raise or a str to
    return). Records the sequence of model ids called."""
    c = CloudRuClient(api_key="test", base_url="https://fm.example/v1")
    called: list[str] = []

    async def fake_create(*, model, messages, max_tokens, temperature, extra_body=None):
        called.append(model)
        outcome = script[model]
        if isinstance(outcome, Exception):
            raise outcome
        return _Resp(outcome)

    monkeypatch.setattr(c._client.chat.completions, "create", fake_create)
    return c, called


def _text(model=ModelName.GLM):
    return ModelCall(model=model, messages=[{"role": "user", "content": "hi"}])


@pytest.mark.parametrize(
    "err",
    [
        _status_err(PermissionDeniedError, 403),
        _status_err(InternalServerError, 500),
        _status_err(RateLimitError, 429),
        _conn_err(),
    ],
)
async def test_falls_back_on_unavailability_errors(monkeypatch, err):
    c, called = _client_with(
        monkeypatch,
        {ModelName.GLM.value: err, ModelName.DEEPSEEK.value: "ok"},
    )
    out = await c.call(_text(ModelName.GLM))
    assert out == "ok"
    assert called == [ModelName.GLM.value, ModelName.DEEPSEEK.value]


async def test_bad_request_is_not_retried(monkeypatch):
    """400 is a client-side error — retrying another model repeats it, so it
    must propagate without touching the fallback."""
    c, called = _client_with(
        monkeypatch,
        {ModelName.GLM.value: _status_err(BadRequestError, 400),
         ModelName.DEEPSEEK.value: "ok"},
    )
    with pytest.raises(BadRequestError):
        await c.call(_text(ModelName.GLM))
    assert called == [ModelName.GLM.value]


async def test_null_content_is_soft_failure(monkeypatch):
    """content=None (GLM thinking-budget trap) falls back to the next model."""
    c, called = _client_with(
        monkeypatch,
        {ModelName.GLM.value: None, ModelName.DEEPSEEK.value: "recovered"},
    )
    out = await c.call(_text(ModelName.GLM))
    assert out == "recovered"
    assert called == [ModelName.GLM.value, ModelName.DEEPSEEK.value]


async def test_chain_exhausted_raises_last_error(monkeypatch):
    c, called = _client_with(
        monkeypatch,
        {ModelName.GLM.value: _status_err(PermissionDeniedError, 403),
         ModelName.DEEPSEEK.value: _status_err(InternalServerError, 500)},
    )
    with pytest.raises(InternalServerError):
        await c.call(_text(ModelName.GLM))
    assert called == [ModelName.GLM.value, ModelName.DEEPSEEK.value]


async def test_vision_never_falls_back_to_text_model(monkeypatch):
    """Kimi is the only permitted multimodal model; a vision call that fails
    must NOT retry a text-only model (it can't see the image)."""
    c, called = _client_with(
        monkeypatch,
        {ModelName.KIMI.value: _status_err(InternalServerError, 500)},
    )
    vision_msg = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    ]
    with pytest.raises(InternalServerError):
        await c.call(ModelCall(model=ModelName.KIMI, messages=vision_msg, max_tokens=2500))
    assert called == [ModelName.KIMI.value]  # no fallback attempted
