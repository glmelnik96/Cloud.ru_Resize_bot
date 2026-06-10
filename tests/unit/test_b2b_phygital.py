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
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, **kwargs):
        return None


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


def _settings_stub(timeout_s: int = 600, prefetch: bool = True, prefetch_delay_s: int = 0):
    return SimpleNamespace(
        use_phygital_render=True,
        phygital_bot_username="Cloud_Phygital_bot",
        phygital_request_timeout_s=timeout_s,
        prefetch_hero=prefetch,
        prefetch_delay_s=prefetch_delay_s,
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


# -------- speculative hero prefetch --------------------------------------


@pytest.mark.asyncio
async def test_prefetch_consumed_on_approve(monkeypatch, tmp_path):
    """A finished prefetch task short-circuits _request_phygital_render:
    resume with the prefetched file, no fresh b2b request."""
    from bot.graph_runner import PREFETCH_KEY

    monkeypatch.setattr("bot.graph_runner.get_settings", lambda: _settings_stub())
    monkeypatch.setattr(
        "bot.graph_runner._render_image_upload",
        lambda *a, **kw: pytest.fail("HITL fallback must not fire when prefetch hit"),
    )
    resume_calls: list[dict] = []

    async def _fake_resume(app, session, decision):
        resume_calls.append(decision)

    monkeypatch.setattr("bot.graph_runner._resume", _fake_resume)

    app = _FakeApp()
    session = _session()

    hero = tmp_path / "prefetched.jpg"
    hero.write_bytes(b"JPEGDATA")

    async def _done_prefetch():
        return {"path": str(hero), "prompt": "prefetched EN prompt", "style": "render"}

    task = asyncio.get_event_loop().create_task(_done_prefetch())
    await asyncio.sleep(0)
    app.bot_data[PREFETCH_KEY] = {session.thread_id: task}

    payload = {"image_prompt": "graph-generated prompt", "image_style": "photo"}
    await asyncio.wait_for(_request_phygital_render(app, session, payload), timeout=2.0)

    assert len(resume_calls) == 1
    decision = resume_calls[0]
    assert decision["action"] == "upload"
    assert decision["local_path"] == str(hero)
    # State gets the prompt/style the image was ACTUALLY rendered with.
    assert decision["prompt"] == "prefetched EN prompt"
    assert decision["style"] == "render"
    # No fresh request left the building.
    assert all(m["chat_id"] != "@Cloud_Phygital_bot" for m in app.bot.sent)
    # Entry consumed.
    assert session.thread_id not in app.bot_data.get(PREFETCH_KEY, {})


@pytest.mark.asyncio
async def test_prefetch_failure_falls_back_to_fresh_request(monkeypatch, tmp_path):
    """A failed prefetch is transparent: the regular b2b request proceeds."""
    from bot.graph_runner import PREFETCH_KEY

    monkeypatch.setattr("bot.graph_runner.get_settings", lambda: _settings_stub())
    monkeypatch.setattr("bot.graph_runner._HEROES_DIR", tmp_path)
    monkeypatch.setattr(
        "bot.graph_runner._render_image_upload",
        lambda *a, **kw: pytest.fail("HITL fallback should not fire on happy path"),
    )
    resume_calls: list[dict] = []

    async def _fake_resume(app, session, decision):
        resume_calls.append(decision)

    monkeypatch.setattr("bot.graph_runner._resume", _fake_resume)

    app = _FakeApp()
    session = _session()

    async def _failing_prefetch():
        raise B2BError("safety_blocked")

    task = asyncio.get_event_loop().create_task(_failing_prefetch())
    await asyncio.sleep(0)
    app.bot_data[PREFETCH_KEY] = {session.thread_id: task}

    payload = {"image_prompt": "graph prompt, no text", "image_style": "render"}
    run = asyncio.create_task(_request_phygital_render(app, session, payload))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    pending = app.bot_data.get(B2B_PENDING_KEY) or {}
    assert len(pending) == 1
    _, entry = next(iter(pending.items()))
    entry["future"].set_result(b"\x89PNGDATA")

    await asyncio.wait_for(run, timeout=2.0)
    assert len(resume_calls) == 1
    assert resume_calls[0]["action"] == "upload"


@pytest.mark.asyncio
async def test_cancel_hero_prefetch_cancels_task():
    from bot.graph_runner import PREFETCH_KEY, cancel_hero_prefetch

    app = _FakeApp()
    session = _session()

    async def _never():
        await asyncio.sleep(3600)

    task = asyncio.get_event_loop().create_task(_never())
    await asyncio.sleep(0)
    app.bot_data[PREFETCH_KEY] = {session.thread_id: task}

    cancel_hero_prefetch(app, session.thread_id)
    await asyncio.sleep(0)

    assert task.cancelled()
    assert session.thread_id not in app.bot_data[PREFETCH_KEY]


@pytest.mark.asyncio
async def test_start_hero_prefetch_noop_when_disabled(monkeypatch):
    from bot.graph_runner import PREFETCH_KEY, start_hero_prefetch

    monkeypatch.setattr(
        "bot.graph_runner.get_settings",
        lambda: _settings_stub(prefetch=False),
    )
    app = _FakeApp()
    session = _session()
    start_hero_prefetch(app, session)
    assert not app.bot_data.get(PREFETCH_KEY)


@pytest.mark.asyncio
async def test_prefetch_hero_full_chain(monkeypatch, tmp_path):
    """_prefetch_hero: snapshot -> style -> prompt -> b2b -> file on disk."""
    from bot.graph_runner import PREFETCH_KEY, _prefetch_hero

    monkeypatch.setattr("bot.graph_runner.get_settings", lambda: _settings_stub())
    monkeypatch.setattr("bot.graph_runner._HEROES_DIR", tmp_path)

    snapshot = SimpleNamespace(values={"winner": {"slogan": "s"}, "session_id": "x"})

    class _FakeGraph:
        async def aget_state(self, config):
            return snapshot

    monkeypatch.setattr("bot.graph_runner._graph", lambda app: _FakeGraph())

    async def _fake_style(state):
        return {"image_style": "isometric"}

    async def _fake_prompt(state):
        assert state["image_style"] == "isometric"
        return {"image_prompt": "EN prompt, no text"}

    monkeypatch.setattr("graph.nodes.route_image_style.route_image_style", _fake_style)
    monkeypatch.setattr(
        "graph.nodes.generate_image_prompt.generate_image_prompt", _fake_prompt
    )

    app = _FakeApp()
    session = _session()

    run = asyncio.create_task(_prefetch_hero(app, session))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    pending = app.bot_data.get(B2B_PENDING_KEY) or {}
    assert len(pending) == 1
    corr, entry = next(iter(pending.items()))
    assert entry["thread_id"] == session.thread_id
    entry["future"].set_result(b"\x89PNGPREFETCH")

    result = await asyncio.wait_for(run, timeout=2.0)
    assert result["style"] == "isometric"
    assert result["prompt"] == "EN prompt, no text"
    assert result["path"].endswith("_b2b_prefetch.jpg")
    # Request really went to the Phygital bot with the corr header.
    req = next(m for m in app.bot.sent if m["chat_id"] == "@Cloud_Phygital_bot")
    assert req["text"].startswith(f"@b2b render 3:4 k2 corr={corr}")
    # Pending entry cleaned up.
    assert corr not in (app.bot_data.get(B2B_PENDING_KEY) or {})
