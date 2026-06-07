"""Unit tests for M3.4 b2b delegation to @Cloud_Phygital_bot.

Covers:
  * regex parsing of @b2b OK / @b2b ERROR replies
  * timeout fallback in _request_phygital_render
  * happy path resolves the pending Future with bytes

We do not spin up PTB; the bot, application, and session objects are
hand-rolled doubles so we can drive the coroutine directly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.graph_runner import (
    B2B_PENDING_KEY,
    B2BError,
    _B2B_ERROR_RE,
    _B2B_OK_RE,
    _request_phygital_render,
)
from bot.sessions import Session


# -------- regex parser --------------------------------------------------


def test_b2b_ok_regex_valid():
    m = _B2B_OK_RE.match("@b2b OK corr=abcd1234")
    assert m is not None
    assert m.group("corr") == "abcd1234"


def test_b2b_ok_regex_rejects_wrong_marker():
    assert _B2B_OK_RE.match("b2b OK corr=abcd1234") is None
    assert _B2B_OK_RE.match("@b2b FAIL corr=abcd1234") is None


def test_b2b_error_regex_with_reason():
    m = _B2B_ERROR_RE.match("@b2b ERROR corr=deadbeef reason=safety_blocked")
    assert m is not None
    assert m.group("corr") == "deadbeef"
    assert m.group("reason") == "safety_blocked"


def test_b2b_error_regex_without_reason():
    m = _B2B_ERROR_RE.match("@b2b ERROR corr=cafebabe")
    assert m is not None
    assert m.group("corr") == "cafebabe"
    assert m.group("reason") is None


def test_b2b_error_regex_corr_unknown():
    m = _B2B_ERROR_RE.match("@b2b ERROR corr=unknown reason=bad_header")
    assert m is not None
    assert m.group("corr") == "unknown"
    assert m.group("reason") == "bad_header"


def test_b2b_error_regex_rejects_garbage():
    assert _B2B_ERROR_RE.match("@b2b ERROR no corr field") is None


# -------- _request_phygital_render --------------------------------------


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, *, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text})


class _FakeApp:
    def __init__(self) -> None:
        self.bot = _FakeBot()
        self.bot_data: dict = {}
        self.tasks: list[asyncio.Task] = []

    def create_task(self, coro):
        t = asyncio.get_event_loop().create_task(coro)
        self.tasks.append(t)
        return t


def _session() -> Session:
    return Session(user_id=42, chat_id=42, thread_id="t" * 32)


def _settings_stub(timeout_s: int = 600):
    return SimpleNamespace(
        use_phygital_render=True,
        phygital_bot_username="Cloud_Phygital_bot",
        phygital_request_timeout_s=timeout_s,
    )


@pytest.mark.asyncio
async def test_request_phygital_render_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.graph_runner.get_settings", lambda: _settings_stub())
    monkeypatch.setattr("bot.graph_runner._HEROES_DIR", tmp_path)
    # Stub out the HITL fallback so a regression that calls it surfaces clearly.
    monkeypatch.setattr(
        "bot.graph_runner._render_image_upload",
        lambda *a, **kw: pytest.fail("HITL fallback should not be invoked on happy path"),
    )
    # Stub out the graph resume — we only want to verify decision payload.
    resume_calls: list[dict] = []

    async def _fake_resume(app, session, decision):
        resume_calls.append(decision)

    monkeypatch.setattr("bot.graph_runner._resume", _fake_resume)

    app = _FakeApp()
    session = _session()
    payload = {
        "kind": "image_upload",
        "image_prompt": "A serene render of a server, no text",
        "image_style": "render",
    }

    task = asyncio.create_task(_request_phygital_render(app, session, payload))
    # Give the coroutine a chance to register the pending Future.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    pending = app.bot_data[B2B_PENDING_KEY]
    assert len(pending) == 1
    corr, entry = next(iter(pending.items()))
    assert entry["thread_id"] == session.thread_id

    # Simulate inbound reply: Phygital bot delivers raw bytes.
    entry["future"].set_result(b"\x89PNG\r\n\x1a\nFAKEDATA")

    await asyncio.wait_for(task, timeout=2.0)

    # Resume was called with the saved local_path.
    assert len(resume_calls) == 1
    decision = resume_calls[0]
    assert decision["action"] == "upload"
    assert decision["style"] == "render"
    assert decision["local_path"].endswith("_b2b.jpg")
    # The file was actually written.
    saved = tmp_path / decision["local_path"].split("/")[-1].split("\\")[-1]
    assert saved.read_bytes().startswith(b"\x89PNG")

    # The outgoing request to @Cloud_Phygital_bot was sent.
    targets = [m["chat_id"] for m in app.bot.sent]
    assert "@Cloud_Phygital_bot" in targets
    request_msg = next(m for m in app.bot.sent if m["chat_id"] == "@Cloud_Phygital_bot")
    assert request_msg["text"].startswith(f"@b2b render 3:4 k2 corr={corr}")
    assert "A serene render" in request_msg["text"]


@pytest.mark.asyncio
async def test_request_phygital_render_timeout_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bot.graph_runner.get_settings", lambda: _settings_stub(timeout_s=0)
    )
    monkeypatch.setattr("bot.graph_runner._HEROES_DIR", tmp_path)

    fallback_calls: list[tuple] = []

    async def _fake_fallback(app, session, payload):
        fallback_calls.append((session, payload))

    monkeypatch.setattr("bot.graph_runner._render_image_upload", _fake_fallback)
    monkeypatch.setattr(
        "bot.graph_runner._resume",
        lambda *a, **kw: pytest.fail("Resume must not be called on timeout"),
    )

    app = _FakeApp()
    session = _session()
    payload = {"image_prompt": "p", "image_style": "render"}

    await _request_phygital_render(app, session, payload)

    assert len(fallback_calls) == 1
    # Pending was cleaned up.
    assert not app.bot_data.get(B2B_PENDING_KEY)


@pytest.mark.asyncio
async def test_request_phygital_render_b2b_error_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr("bot.graph_runner.get_settings", lambda: _settings_stub())
    monkeypatch.setattr("bot.graph_runner._HEROES_DIR", tmp_path)

    fallback_calls: list[tuple] = []

    async def _fake_fallback(app, session, payload):
        fallback_calls.append((session, payload))

    monkeypatch.setattr("bot.graph_runner._render_image_upload", _fake_fallback)
    monkeypatch.setattr(
        "bot.graph_runner._resume",
        lambda *a, **kw: pytest.fail("Resume must not be called on error"),
    )

    app = _FakeApp()
    session = _session()
    payload = {"image_prompt": "p", "image_style": "render"}

    task = asyncio.create_task(_request_phygital_render(app, session, payload))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    pending = app.bot_data[B2B_PENDING_KEY]
    corr, entry = next(iter(pending.items()))
    entry["future"].set_exception(B2BError("safety_blocked"))

    await asyncio.wait_for(task, timeout=2.0)

    assert len(fallback_calls) == 1
    assert not app.bot_data.get(B2B_PENDING_KEY)


@pytest.mark.asyncio
async def test_request_phygital_render_empty_prompt_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr("bot.graph_runner.get_settings", lambda: _settings_stub())
    monkeypatch.setattr("bot.graph_runner._HEROES_DIR", tmp_path)

    fallback_calls: list[tuple] = []

    async def _fake_fallback(app, session, payload):
        fallback_calls.append((session, payload))

    monkeypatch.setattr("bot.graph_runner._render_image_upload", _fake_fallback)

    app = _FakeApp()
    session = _session()
    payload = {"image_prompt": "   ", "image_style": "render"}

    await _request_phygital_render(app, session, payload)

    assert len(fallback_calls) == 1
    # Did not even register a pending Future.
    assert not app.bot_data.get(B2B_PENDING_KEY)
    # Did not try to send any TG messages.
    assert app.bot.sent == []
