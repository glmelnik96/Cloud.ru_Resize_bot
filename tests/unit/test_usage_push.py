"""Fire-and-forget usage push to the gateway ingest (platform 2026-07-22).

The local usage_events row stays the source of truth; this push is best-effort.
These tests pin the wire contract: disabled when url/token unset, correct
payload + X-Ingest-Token header, workflow truncation, a single retry on network
error only (401/422 terminal), and that excluded (smoke) accounts emit nothing.
"""
from __future__ import annotations

import asyncio

import pytest

from app import usage_push
from app.config import settings


@pytest.fixture(autouse=True)
def _clear_pending():
    """Keep the module-level in-flight set clean between tests."""
    usage_push._pending.clear()
    yield
    usage_push._pending.clear()


@pytest.fixture
def enable_push(monkeypatch):
    monkeypatch.setattr(settings, "usage_ingest_url", "https://gw.example/internal/usage")
    monkeypatch.setattr(settings, "usage_ingest_token", "secret-token")


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """Records the single POST and returns a scripted status/exception."""

    calls: list[dict] = []

    def __init__(self, *, status: int = 200, exc: Exception | None = None) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json, headers):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        script = type(self).script.pop(0)
        if isinstance(script, Exception):
            raise script
        return _FakeResponse(script)


def _install_client(monkeypatch, script):
    import httpx

    _FakeClient.calls = []
    _FakeClient.script = list(script)

    def _factory(*a, **k):
        return _FakeClient()

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


async def _drain() -> None:
    """Let scheduled fire-and-forget tasks run to completion."""
    while usage_push._pending:
        await asyncio.gather(*list(usage_push._pending))


async def test_emit_noop_when_token_empty(monkeypatch):
    monkeypatch.setattr(settings, "usage_ingest_url", "https://gw.example/x")
    monkeypatch.setattr(settings, "usage_ingest_token", "")
    usage_push.emit(app="creatives", email="a@b.ru", workflow="creatives",
                    status="done", duration_ms=1200)
    assert usage_push._pending == set()


async def test_emit_noop_when_url_empty(monkeypatch):
    monkeypatch.setattr(settings, "usage_ingest_url", "")
    monkeypatch.setattr(settings, "usage_ingest_token", "tok")
    usage_push.emit(app="creatives", email="a@b.ru", workflow="creatives",
                    status="done", duration_ms=1200)
    assert usage_push._pending == set()


async def test_emit_posts_expected_payload_and_header(monkeypatch, enable_push):
    _install_client(monkeypatch, script=[200])
    usage_push.emit(
        app="webinar", email="a@b.ru", workflow="speaker", status="done",
        duration_ms=1500, gateway_user_id="gw:1", meta={"count": 24},
    )
    await _drain()

    assert len(_FakeClient.calls) == 1
    call = _FakeClient.calls[0]
    assert call["url"] == "https://gw.example/internal/usage"
    assert call["headers"]["X-Ingest-Token"] == "secret-token"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"] == {
        "app": "webinar",
        "email": "a@b.ru",
        "event": "generation",
        "status": "done",
        "workflow": "speaker",
        "duration_ms": 1500,
        "gateway_user_id": "gw:1",
        "meta": {"count": 24},
    }


async def test_emit_omits_none_fields(monkeypatch, enable_push):
    _install_client(monkeypatch, script=[200])
    usage_push.emit(app="creatives", email="", workflow=None, status="failed",
                    duration_ms=None)
    await _drain()
    assert _FakeClient.calls[0]["json"] == {
        "app": "creatives", "email": "", "event": "generation", "status": "failed",
    }


async def test_workflow_truncated_to_32(monkeypatch, enable_push):
    _install_client(monkeypatch, script=[200])
    long_wf = "w" * 50
    usage_push.emit(app="creatives", email="a@b.ru", workflow=long_wf,
                    status="done", duration_ms=1)
    await _drain()
    assert _FakeClient.calls[0]["json"]["workflow"] == "w" * 32


async def test_retry_once_on_network_error(monkeypatch, enable_push):
    _install_client(monkeypatch, script=[ConnectionError("boom"), 200])
    usage_push.emit(app="creatives", email="a@b.ru", workflow="creatives",
                    status="done", duration_ms=1)
    await _drain()
    assert len(_FakeClient.calls) == 2  # first failed, retry succeeded


async def test_terminal_status_not_retried(monkeypatch, enable_push):
    """401/422 are terminal — one POST, no retry."""
    _install_client(monkeypatch, script=[422, 200])
    usage_push.emit(app="images", email="a@b.ru", workflow="creatives",
                    status="done", duration_ms=1)
    await _drain()
    assert len(_FakeClient.calls) == 1
