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


class _FakeGraph:
    """astream: emit a couple node updates, then park at a text interrupt."""

    def __init__(self, interrupt_value):
        self._iv = interrupt_value

    async def astream(self, payload, config=None, stream_mode=None):
        yield {"parse_brief": {"brief": {"product": "x"}}}
        yield {"generate_message_candidates": {"candidates": []}}
        yield {"__interrupt__": [_FakeInterrupt(self._iv)]}


async def _sessionmaker(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'orch.db'}")
    await init_db(engine)
    return make_sessionmaker(engine)


def _service(Session, graph, **kw):
    return CreativesService(
        manager=TaskManager(tmp_root=kw.get("tmp", "./data/tmp_test")),
        bus=kw.get("bus", EventBus()),
        sessionmaker=Session,
        graph=graph,
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
