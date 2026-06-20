"""App3 Phase 2 — orchestrator segment driver + create() gate.

Uses a fake compiled graph (no Redis, no LLM) to validate that the first
segment parks at awaiting_text and publishes the decision payload, and that
create() persists a queued task + enforces the per-user open-session gate.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from app.db import models  # noqa: E402
from app.db.database import init_db, make_engine, make_sessionmaker  # noqa: E402
from app.services.creatives import (  # noqa: E402
    CapacityError,
    CreativesService,
    build_raw_brief,
)
from app.tasks.events import EventBus  # noqa: E402
from app.tasks.manager import TaskManager  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────
class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


class _FakeState:
    def __init__(self, values):
        self.values = values


class _FakeGraph:
    """astream: emit a couple node updates, then park at a text interrupt."""

    def __init__(self, interrupt_value):
        self._iv = interrupt_value

    async def astream(self, payload, config=None, stream_mode=None):
        yield {"parse_brief": {"brief": {"product": "x"}}}
        yield {"generate_message_candidates": {"candidates": []}}
        yield {"__interrupt__": [_FakeInterrupt(self._iv)]}


class _ResumableGraph:
    """Branches on the input: initial → text interrupt; Command(resume=approve)
    → image interrupt; Command(resume=cancel) → terminal cancelled."""

    def __init__(self):
        self.values = {"winner": {"slogan": "S"}, "image_prompt": "P", "image_style": "render"}

    async def astream(self, payload, config=None, stream_mode=None):
        resume = getattr(payload, "resume", None)
        if resume is None:
            yield {"__interrupt__": [_FakeInterrupt({"kind": "text_approve", "candidate": {"slogan": "S"}})]}
            return
        action = resume.get("action")
        if action == "approve":
            yield {"route_image_style": {"image_style": "render"}}
            yield {"__interrupt__": [_FakeInterrupt(
                {"kind": "image_upload", "image_prompt": "P", "image_style": "render"}
            )]}
        elif action == "cancel":
            yield {"hitl_text_approve": {"cancelled": True}}
        else:  # regenerate/refine → back to a fresh text interrupt
            yield {"__interrupt__": [_FakeInterrupt({"kind": "text_approve", "candidate": {"slogan": "S2"}})]}

    async def aget_state(self, config):
        return _FakeState(self.values)


class _TerminalGraph:
    """Resume(upload) → terminal with rendered files + zip."""

    def __init__(self, files, zip_path):
        self._files = files
        self._zip = zip_path

    async def astream(self, payload, config=None, stream_mode=None):
        yield {"fill_templates_per_format": {"rendered_files": self._files}}
        yield {"render_all": {"rendered_zip_path": self._zip}}


async def _sessionmaker(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'orch.db'}")
    await init_db(engine)
    return make_sessionmaker(engine)


class _FakeHeroGen:
    """Available generator that writes a stub PNG to dest."""

    available = True

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.seen = None

    async def generate(self, *, prompt, style, dest):
        if self.fail:
            raise RuntimeError("boom")
        self.seen = (prompt, style)
        from pathlib import Path as _P

        _P(dest).write_bytes(b"\x89PNG-gen")
        return dest


def _service(Session, graph, **kw):
    return CreativesService(
        manager=TaskManager(tmp_root=kw.get("tmp", "./data/tmp_test")),
        bus=kw.get("bus", EventBus()),
        sessionmaker=Session,
        graph=graph,
        results_dir=kw.get("results_dir", "./data/results_test"),
        hero_generator=kw.get("hero_generator"),
        max_open_per_user=kw.get("max_open", 5),
    )


# ── tests ──────────────────────────────────────────────────────
def test_build_raw_brief():
    txt = build_raw_brief({"product": "Облако", "goal": "conversion", "audience": "DevOps"})
    assert "Продукт: Облако" in txt
    assert "Цель: conversion" in txt
    assert "ЦА: DevOps" in txt


@pytest.mark.asyncio
async def test_segment_parks_at_awaiting_text(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    iv = {"kind": "text_approve", "candidate": {"slogan": "Быстро в облако"}}
    svc = _service(Session, _FakeGraph(iv), bus=bus, tmp=tmp_path / "tmp")

    # seed a queued task row
    async with Session() as s:
        s.add(models.Task(task_uid="t1", user_id=1, workflow="creatives", status="queued"))
        await s.commit()

    q = bus.subscribe("t1")
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t1", label="creatives", eta_sec=None)
    await svc._run_segment("t1", "1", {"raw_brief": "x"}, reporter)

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t1"))
        task = res.scalar_one()
    assert task.status == "awaiting_text"
    assert task.started_at is not None

    # the awaiting_input event carries the candidate for the UI
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"]
    assert awaiting and awaiting[0]["phase"] == "text_approve"
    assert awaiting[0]["candidate"]["slogan"] == "Быстро в облако"


@pytest.mark.asyncio
async def test_create_persists_queued_and_enqueues(tmp_path, monkeypatch):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _FakeGraph({"kind": "text_approve"}), tmp=tmp_path / "tmp")

    captured = {}

    async def fake_submit(user_id, runner):
        captured["user_id"] = user_id
        captured["runner"] = runner
        return True

    monkeypatch.setattr(svc.manager, "submit", fake_submit)
    uid = await svc.create("1", {"product": "p", "goal": "g", "audience": "a"})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == uid))
        task = res.scalar_one()
    assert task.status == "queued"
    assert task.workflow == "creatives"
    assert task.params["product"] == "p"
    assert captured["user_id"] == "1" and callable(captured["runner"])


@pytest.mark.asyncio
async def test_create_rejects_when_too_many_open(tmp_path):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _FakeGraph({"kind": "text_approve"}), tmp=tmp_path / "tmp", max_open=2)
    async with Session() as s:
        for i in range(2):
            s.add(models.Task(task_uid=f"o{i}", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()
    with pytest.raises(CapacityError):
        await svc.create("1", {"product": "p", "goal": "g", "audience": "a"})


@pytest.mark.asyncio
async def test_approve_advances_to_awaiting_image(tmp_path):
    """Resuming a parked text task with approve runs the next segment and parks
    at awaiting_image, publishing the image_prompt for the UI."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(
        Session, _ResumableGraph(), bus=bus, tmp=tmp_path / "tmp",
        hero_generator=_FakeHeroGen(),
    )
    async with Session() as s:
        s.add(models.Task(task_uid="t2", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    q = bus.subscribe("t2")
    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t2", label="creatives", eta_sec=None)
    await svc._run_segment(
        "t2", "1", Command(resume={"action": "approve", "comment": None}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t2"))
        task = res.scalar_one()
    assert task.status == "awaiting_image"
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"]
    assert awaiting and awaiting[0]["phase"] == "image_upload"
    assert awaiting[0]["image_prompt"] == "P"
    assert awaiting[0]["can_generate"] is True


@pytest.mark.asyncio
async def test_cancel_reaches_terminal_cancelled(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(Session, _ResumableGraph(), bus=bus, tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="t3", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    q = bus.subscribe("t3")
    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t3", label="creatives", eta_sec=None)
    await svc._run_segment(
        "t3", "1", Command(resume={"action": "cancel", "comment": None}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t3"))
        task = res.scalar_one()
    assert task.status == "cancelled"
    assert task.finished_at is not None
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["kind"] == "cancelled" for e in events)


@pytest.mark.asyncio
async def test_pending_rehydrates_from_checkpoint(tmp_path):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    text_payload = await svc.pending("tX", "awaiting_text")
    assert text_payload["phase"] == "text_approve"
    assert text_payload["candidate"] == {"slogan": "S"}
    img_payload = await svc.pending("tX", "awaiting_image")
    assert img_payload["phase"] == "image_upload"
    assert img_payload["image_prompt"] == "P"
    assert await svc.pending("tX", "done") is None


@pytest.mark.asyncio
async def test_terminal_collects_results_to_results_dir(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    # fake rendered outputs on disk
    src = tmp_path / "renders"
    src.mkdir()
    png = src / "banner_300x250.png"
    png.write_bytes(b"\x89PNG-fake")
    zp = tmp_path / "out.zip"
    zp.write_bytes(b"PK-fake-zip")

    results = tmp_path / "results"
    svc = _service(
        Session,
        _TerminalGraph([{"format": "banner_300x250", "path": str(png)}], str(zp)),
        bus=bus, tmp=tmp_path / "tmp", results_dir=results,
    )
    async with Session() as s:
        s.add(models.Task(task_uid="t5", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t5", label="creatives", eta_sec=None)
    await svc._run_segment(
        "t5", "1", Command(resume={"action": "upload", "local_path": "x"}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t5"))
        task = res.scalar_one()
    assert task.status == "done"
    assert task.result_url == "/results/t5/t5.zip"
    assert (results / "t5" / "t5.zip").exists()
    assert (results / "t5" / "banner_300x250.png").exists()


@pytest.mark.asyncio
async def test_collect_results_png_fallback_when_no_zip(tmp_path):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    url = svc._collect_results("u1", {"rendered_files": [{"format": "f1", "path": str(png)}]})
    assert url == "/results/u1/f1.png"
    assert (tmp_path / "res" / "u1" / "f1.png").exists()


@pytest.mark.asyncio
async def test_null_hero_generator_unavailable():
    from app.services.hero_gen import HeroGenUnavailable, NullHeroGenerator

    g = NullHeroGenerator()
    assert g.available is False
    with pytest.raises(HeroGenUnavailable):
        await g.generate(prompt="x", style="render", dest="y")


def test_make_hero_generator_selects_backend():
    from app.services.hero_gen import (
        App1HeroGenerator,
        NullHeroGenerator,
        make_hero_generator,
    )

    assert isinstance(
        make_hero_generator(backend="app1", app1_hero_url="http://127.0.0.1:8011/internal/hero"),
        App1HeroGenerator,
    )
    assert isinstance(make_hero_generator(backend="none", app1_hero_url="x"), NullHeroGenerator)
    # unknown backend → safe default (manual upload)
    assert isinstance(make_hero_generator(backend="???", app1_hero_url="x"), NullHeroGenerator)


@pytest.mark.asyncio
async def test_app1_hero_generator_image_response(tmp_path, monkeypatch):
    """App1HeroGenerator writes image bytes from an image/* response to dest."""
    from app.services import hero_gen as hg

    class _Resp:
        def __init__(self, content=b"", headers=None, json_data=None):
            self.content = content
            self.headers = headers or {}
            self._json = json_data

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json):
            return _Resp(content=b"\x89PNG-app1", headers={"content-type": "image/png"})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    gen = hg.App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    dest = tmp_path / "hero.png"
    out = await gen.generate(prompt="cloud", style="render", dest=dest)
    assert out == dest
    assert dest.read_bytes() == b"\x89PNG-app1"


@pytest.mark.asyncio
async def test_app1_hero_generator_failure_raises_unavailable(tmp_path, monkeypatch):
    from app.services import hero_gen as hg

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json):
            raise RuntimeError("connection refused")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    gen = hg.App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    with pytest.raises(hg.HeroGenUnavailable):
        await gen.generate(prompt="x", style="render", dest=tmp_path / "h.png")


@pytest.mark.asyncio
async def test_awaiting_image_can_generate_reflects_backend(tmp_path):
    """can_generate in the awaiting_image event mirrors generator.available."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(
        Session, _ResumableGraph(), bus=bus, tmp=tmp_path / "tmp",
        hero_generator=_FakeHeroGen(),
    )
    async with Session() as s:
        s.add(models.Task(task_uid="g0", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()
    q = bus.subscribe("g0")
    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="g0", label="creatives", eta_sec=None)
    await svc._run_segment("g0", "1", Command(resume={"action": "approve"}), reporter)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"][0]
    assert awaiting["can_generate"] is True


@pytest.mark.asyncio
async def test_generate_decision_unavailable_raises(tmp_path):
    from app.services.hero_gen import HeroGenUnavailable

    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")  # default Null
    with pytest.raises(HeroGenUnavailable):
        await svc.generate_decision("gx", "1")


@pytest.mark.asyncio
async def test_generate_decision_success_runs_to_done(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    png = tmp_path / "r.png"
    png.write_bytes(b"x")
    zp = tmp_path / "r.zip"
    zp.write_bytes(b"z")
    graph = _TerminalGraph([{"format": "f1", "path": str(png)}], str(zp))
    # _TerminalGraph needs aget_state for prompt fetch
    graph.values = {"image_prompt": "P", "image_style": "render"}

    async def _aget_state(config):
        return _FakeState(graph.values)

    graph.aget_state = _aget_state
    gen = _FakeHeroGen()
    svc = _service(
        Session, graph, bus=bus, tmp=tmp_path / "tmp",
        results_dir=tmp_path / "res", hero_generator=gen,
    )
    async with Session() as s:
        s.add(models.Task(task_uid="g1", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    # generate_decision enqueues a runner on the manager; run it inline instead
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.generate_decision("g1", "1")
    await captured["runner"]()

    assert gen.seen == ("P", "render")
    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "g1"))
        task = res.scalar_one()
    assert task.status == "done"


@pytest.mark.asyncio
async def test_generate_decision_failure_reparks(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    graph = _ResumableGraph()
    svc = _service(
        Session, graph, bus=bus, tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen(fail=True)
    )
    async with Session() as s:
        s.add(models.Task(task_uid="g2", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.generate_decision("g2", "1")
    await captured["runner"]()

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "g2"))
        task = res.scalar_one()
    assert task.status == "awaiting_image"  # re-parked for manual upload


@pytest.mark.asyncio
async def test_submit_decision_flips_to_running_then_enqueues(tmp_path, monkeypatch):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="t4", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    submitted = {}

    async def fake_submit(user_id, runner):
        submitted["called"] = True
        return True

    monkeypatch.setattr(svc.manager, "submit", fake_submit)
    await svc.submit_decision("t4", "1", {"action": "approve", "comment": None})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t4"))
        task = res.scalar_one()
    # status flips to running synchronously (closes the double-submit window)
    assert task.status == "running"
    assert submitted.get("called") is True
