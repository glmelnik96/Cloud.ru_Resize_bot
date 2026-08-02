"""Usage-metric shape asked for by the platform (App1, 2026-08-02).

Two additions to the whitelisted `meta` of every terminal usage row:

* ``reason`` — why the task ended, so the admin block can tell a deliberate
  user cancel from an abandoned session, and a real pipeline failure from a
  row we closed ourselves while the service was stopping (a deploy). Without
  it App1 reads our restarts as unreliability and goes fixing what isn't broken.
* ``work_ms`` — time the task actually computed, excluding the HITL pauses.
  ``duration_ms`` is wall-clock and includes them, so a successful build where
  the user approved the text next morning honestly reports 15 hours (measured
  on prod: mean 65 min vs median 9.3 min over 25 done rows).
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from app.db import models  # noqa: E402
from app.db.database import init_db, make_engine, make_sessionmaker  # noqa: E402
from app.services.creatives import CreativesService  # noqa: E402
from app.tasks.events import EventBus  # noqa: E402
from app.tasks.manager import TaskManager  # noqa: E402
from app.tasks.status import WebStatusReporter  # noqa: E402


async def _sm(tmp_path, name="usage.db"):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    await init_db(engine)
    return make_sessionmaker(engine)


async def _events(Session) -> list[models.UsageEvent]:
    async with Session() as s:
        res = await s.execute(select(models.UsageEvent).order_by(models.UsageEvent.id))
        return list(res.scalars())


async def _only_event(Session) -> models.UsageEvent:
    rows = await _events(Session)
    assert len(rows) == 1, f"expected exactly one usage row, got {len(rows)}"
    return rows[0]


def _service(Session, graph, tmp_path, **kw):
    return CreativesService(
        manager=kw.get("manager") or TaskManager(tmp_root=tmp_path / "tmp"),
        bus=EventBus(),
        sessionmaker=Session,
        graph=graph,
        results_dir=tmp_path / "res",
        park_timeout_sec=kw.get("park_timeout_sec", 24 * 3600),
    )


def _reporter(svc, uid):
    return WebStatusReporter(svc.bus, task_uid=uid, label="creatives", eta_sec=None)


class _Interrupt:
    def __init__(self, value):
        self.value = value


class _PauseGraph:
    """Parks at text, then at image, then cancels — one interrupt per resume."""

    values: dict = {}

    async def astream(self, payload, config=None, stream_mode=None):
        resume = getattr(payload, "resume", None)
        if resume is None:
            yield {"__interrupt__": [_Interrupt({"kind": "text_approve", "candidates": []})]}
        elif resume.get("action") == "approve":
            yield {"__interrupt__": [_Interrupt({"kind": "image_upload", "image_prompt": "p"})]}
        else:
            yield {"hitl": {"cancelled": True}}

    async def aget_state(self, config):
        raise RuntimeError("no checkpoint in this fake")


class _BoomGraph:
    async def astream(self, payload, config=None, stream_mode=None):
        raise RuntimeError("llm exploded")
        yield  # pragma: no cover — makes this an async generator


class _CancelGraph:
    async def astream(self, payload, config=None, stream_mode=None):
        raise asyncio.CancelledError()
        yield  # pragma: no cover — makes this an async generator


async def _seed(Session, uid="t1", status="queued", workflow="creatives"):
    async with Session() as s:
        s.add(
            models.Task(task_uid=uid, user_id=1, workflow=workflow, status=status)
        )
        await s.commit()


# ── reason ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_user_cancel_is_reason_user(tmp_path):
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _PauseGraph(), tmp_path)

    await svc._finish_terminal("t1", _reporter(svc, "t1"), {"cancelled": True})

    row = await _only_event(Session)
    assert row.status == "cancelled"
    assert row.meta["reason"] == "user"


@pytest.mark.asyncio
async def test_upload_timeout_is_reason_timeout(tmp_path):
    """A session abandoned at the hero step must not read as a deliberate
    'no thanks' — the two need different fixes."""
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _PauseGraph(), tmp_path)

    await svc._finish_terminal(
        "t1", _reporter(svc, "t1"),
        {"cancelled": True, "error": "image_upload_timeout"},
    )

    row = await _only_event(Session)
    assert row.status == "cancelled"
    assert row.meta["reason"] == "timeout"


@pytest.mark.asyncio
async def test_text_approve_timeout_is_reason_timeout(tmp_path):
    """A session abandoned at the propositions is an abandoned session too —
    same reason as the hero one, not a deliberate 'no thanks'."""
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _PauseGraph(), tmp_path)

    await svc._finish_terminal(
        "t1", _reporter(svc, "t1"),
        {"cancelled": True, "error": "text_approve_timeout"},
    )

    row = await _only_event(Session)
    assert row.status == "cancelled"
    assert row.meta["reason"] == "timeout"


@pytest.mark.asyncio
async def test_rearm_sweep_closes_with_reason_timeout(tmp_path):
    """The restart sweep closes expired parked rows straight in the DB — that
    path must carry the same reason as the live timer."""
    from datetime import datetime, timedelta, timezone

    Session = await _sm(tmp_path)
    async with Session() as s:
        t = models.Task(task_uid="t1", user_id=1, workflow="creatives", status="awaiting_image")
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=48)
        s.add(t)
        await s.commit()

    svc = _service(Session, object(), tmp_path)
    assert await svc.rearm_parked_timeouts() == 1

    row = await _only_event(Session)
    assert row.status == "cancelled"
    assert row.meta["reason"] == "timeout"


@pytest.mark.asyncio
async def test_pipeline_failure_is_reason_error(tmp_path):
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _BoomGraph(), tmp_path)

    await svc._run_segment("t1", "1", {}, _reporter(svc, "t1"))

    row = await _only_event(Session)
    assert row.status == "failed"
    assert row.meta["reason"] == "error"


@pytest.mark.asyncio
async def test_shutdown_cancel_is_reason_shutdown(tmp_path):
    """Rows we close ourselves while the process stops (i.e. our own deploys)
    must be separable from real pipeline failures."""
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _CancelGraph(), tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await svc._run_segment("t1", "1", {}, _reporter(svc, "t1"))

    row = await _only_event(Session)
    assert row.status == "failed"
    assert row.meta["reason"] == "shutdown"


@pytest.mark.asyncio
async def test_queue_full_is_reason_queue_full(tmp_path):
    """Capacity rejection is not a pipeline defect either."""
    from app.services.creatives import CapacityError

    Session = await _sm(tmp_path)
    manager = TaskManager(tmp_root=tmp_path / "tmp")

    async def _full(user_id, runner):
        return False

    manager.submit = _full  # type: ignore[assignment]
    svc = _service(Session, _PauseGraph(), tmp_path, manager=manager)

    with pytest.raises(CapacityError):
        await svc.create(1, {"product": "p", "audience": "a", "emotion": "e"})

    row = await _only_event(Session)
    assert row.status == "failed"
    assert row.meta["reason"] == "queue_full"


@pytest.mark.asyncio
async def test_done_carries_no_reason(tmp_path):
    """`reason` explains an early end; a success has none to give."""
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _PauseGraph(), tmp_path)

    await svc._finish_terminal("t1", _reporter(svc, "t1"), {"rendered_files": []})

    row = await _only_event(Session)
    assert row.status == "done"
    assert "reason" not in row.meta
    assert row.meta["ratios"] == ["300x600"]


# ── work_ms ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_work_ms_excludes_the_hitl_pause(tmp_path):
    """The whole point: a task parked for a long time must not report that
    wait as compute. duration_ms keeps the wall-clock; work_ms must not."""
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _PauseGraph(), tmp_path)
    rep = _reporter(svc, "t1")

    await svc._run_segment("t1", "1", {}, rep)  # → awaiting_text
    async with Session() as s:
        task = (await s.execute(
            select(models.Task).where(models.Task.task_uid == "t1")
        )).scalar_one()
        assert task.status == "awaiting_text"
    # parking at the text stop must arm its deadline, same as the image one
    assert "t1" in svc._timeouts

    await asyncio.sleep(0.3)  # the human thinks

    from langgraph.types import Command

    await svc._run_segment("t1", "1", Command(resume={"action": "cancel"}), rep)

    row = await _only_event(Session)
    assert row.status == "cancelled"
    assert row.duration_ms >= 300, "wall-clock must still include the pause"
    assert row.meta["work_ms"] < 250, (
        f"work_ms={row.meta['work_ms']} swallowed the 300ms pause"
    )


@pytest.mark.asyncio
async def test_work_ms_accumulates_across_segments(tmp_path):
    """Compute happens in several segments split by pauses — they must add up,
    not overwrite each other."""
    Session = await _sm(tmp_path)
    await _seed(Session)
    svc = _service(Session, _PauseGraph(), tmp_path)
    rep = _reporter(svc, "t1")

    await svc._run_segment("t1", "1", {}, rep)
    async with Session() as s:
        task = (await s.execute(
            select(models.Task).where(models.Task.task_uid == "t1")
        )).scalar_one()
        after_first = task.timings.get("work_ms")
    assert after_first is not None and after_first >= 0

    from langgraph.types import Command

    await svc.submit_decision("t1", "1", {"action": "approve"})
    await asyncio.sleep(0.05)  # let the lane run the resumed segment

    async with Session() as s:
        task = (await s.execute(
            select(models.Task).where(models.Task.task_uid == "t1")
        )).scalar_one()
        assert task.status == "awaiting_image"
        assert task.timings["work_ms"] >= after_first


@pytest.mark.asyncio
async def test_work_ms_present_on_every_terminal_status(tmp_path):
    """The gateway reads one field for every row; a missing key on some
    statuses would push a special case into their query."""
    Session = await _sm(tmp_path)
    await _seed(Session, uid="ok")
    await _seed(Session, uid="bad")
    svc_ok = _service(Session, _PauseGraph(), tmp_path)
    await svc_ok._finish_terminal("ok", _reporter(svc_ok, "ok"), {"rendered_files": []})
    svc_bad = _service(Session, _BoomGraph(), tmp_path)
    await svc_bad._run_segment("bad", "1", {}, _reporter(svc_bad, "bad"))

    rows = await _events(Session)
    assert len(rows) == 2
    for row in rows:
        assert isinstance(row.meta.get("work_ms"), int), row.meta


# ── webinar ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_webinar_terminal_carries_same_fields(tmp_path):
    """Webinar has no HITL pause, so work_ms ≈ duration_ms — but the field must
    exist there too, otherwise 'use duration_ms for webinar' becomes an unwritten
    rule in the gateway (exactly what the app/workflow mess already cost us)."""
    from app.services.webinar import WebinarService

    Session = await _sm(tmp_path)
    await _seed(Session, uid="w1", workflow="webinar")

    svc = WebinarService.__new__(WebinarService)
    svc.Session = Session

    await svc._set_status("w1", "running")
    await asyncio.sleep(0.05)
    await svc._finish("w1", "done", meta={"variant": "speaker", "count": 26})

    row = await _only_event(Session)
    assert row.app == "webinar"
    assert row.status == "done"
    assert "reason" not in row.meta
    assert row.meta["count"] == 26
    assert isinstance(row.meta["work_ms"], int)
    assert row.meta["work_ms"] >= 40


@pytest.mark.asyncio
async def test_webinar_failure_carries_reason(tmp_path):
    from app.services.webinar import WebinarService

    Session = await _sm(tmp_path)
    await _seed(Session, uid="w1", workflow="webinar")

    svc = WebinarService.__new__(WebinarService)
    svc.Session = Session

    await svc._finish("w1", "failed", error="boom", reason="error")

    row = await _only_event(Session)
    assert row.status == "failed"
    assert row.meta["reason"] == "error"
