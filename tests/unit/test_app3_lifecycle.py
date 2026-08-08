"""App3 Phase 6 — reconcile-after-restart, retention, HITL park timeout."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from app.db import models  # noqa: E402
from app.db.database import init_db, make_engine, make_sessionmaker  # noqa: E402
from app.tasks.reconcile import reconcile_interrupted_tasks  # noqa: E402
from app.tasks.retention import purge_once  # noqa: E402


async def _sm(tmp_path, name="lc.db"):
    eng = make_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    await init_db(eng)
    return make_sessionmaker(eng)


@pytest.mark.asyncio
async def test_reconcile_fails_orphans_keeps_parked(tmp_path):
    Session = await _sm(tmp_path)
    async with Session() as s:
        s.add_all([
            models.Task(task_uid="q", user_id=1, workflow="creatives", status="queued"),
            models.Task(task_uid="r", user_id=1, workflow="creatives", status="running"),
            models.Task(task_uid="at", user_id=1, workflow="creatives", status="awaiting_text"),
            models.Task(task_uid="ai", user_id=1, workflow="creatives", status="awaiting_image"),
            models.Task(task_uid="d", user_id=1, workflow="creatives", status="done"),
        ])
        await s.commit()

    n = await reconcile_interrupted_tasks(Session)
    assert n == 2  # only queued + running

    async with Session() as s:
        rows = {t.task_uid: t.status for t in (await s.execute(select(models.Task))).scalars()}
    assert rows["q"] == "failed"
    assert rows["r"] == "failed"
    assert rows["at"] == "awaiting_text"  # parked survives
    assert rows["ai"] == "awaiting_image"
    assert rows["d"] == "done"


@pytest.mark.asyncio
async def test_retention_purges_old_terminal_keeps_open_and_fresh(tmp_path):
    Session = await _sm(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    old_dir = results / "old"
    old_dir.mkdir()
    (old_dir / "a.png").write_bytes(b"x")
    fresh_dir = results / "fresh"
    fresh_dir.mkdir()
    (fresh_dir / "b.png").write_bytes(b"y")
    # age the old dir past the TTL
    old_t = time.time() - 48 * 3600
    import os

    os.utime(old_dir, (old_t, old_t))

    now = datetime.now(timezone.utc)
    async with Session() as s:
        # old terminal row → purged; old open row → kept; fresh row → kept
        t_old = models.Task(task_uid="told", user_id=1, workflow="creatives", status="done")
        t_old.created_at = now - timedelta(hours=48)
        t_open = models.Task(task_uid="topen", user_id=1, workflow="creatives", status="awaiting_text")
        t_open.created_at = now - timedelta(hours=48)
        s.add_all([t_old, t_open])
        await s.commit()

    dirs, rows = await purge_once(results_dir=results, sessionmaker=Session, ttl_sec=24 * 3600)
    assert dirs == 1  # only old dir
    assert rows == 1  # only old terminal row
    assert not old_dir.exists()
    assert fresh_dir.exists()
    async with Session() as s:
        remaining = {t.task_uid for t in (await s.execute(select(models.Task))).scalars()}
    assert "topen" in remaining  # open task kept despite age
    assert "told" not in remaining


@pytest.mark.asyncio
async def test_image_timeout_fires_when_still_parked(tmp_path):
    """Armed timeout resumes the graph with {action:timeout} if the task is
    still awaiting_image when it elapses."""
    from app.services.creatives import CreativesService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    Session = await _sm(tmp_path)
    async with Session() as s:
        s.add(models.Task(task_uid="to", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    svc = CreativesService(
        manager=TaskManager(tmp_root=tmp_path / "tmp"), bus=EventBus(),
        sessionmaker=Session, graph=object(), results_dir=tmp_path / "res",
        park_timeout_sec=0,  # fire immediately
    )
    captured = {}

    async def fake_submit_decision(uid, user_id, decision):
        captured["decision"] = decision

    svc.submit_decision = fake_submit_decision  # type: ignore[assignment]
    svc._arm_park_timeout("to", "awaiting_image")
    # let the 0-sec sleeper run
    import asyncio

    await asyncio.sleep(0.05)
    assert captured.get("decision") == {"action": "timeout"}


def _svc(Session, tmp_path, *, timeout_sec):
    from app.services.creatives import CreativesService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    # graph=object() on purpose: the restart sweep must never resume a stale
    # checkpoint, so any graph access here explodes the test.
    return CreativesService(
        manager=TaskManager(tmp_root=tmp_path / "tmp"), bus=EventBus(),
        sessionmaker=Session, graph=object(), results_dir=tmp_path / "res",
        park_timeout_sec=timeout_sec,
    )


@pytest.mark.asyncio
async def test_rearm_closes_parked_task_whose_deadline_passed(tmp_path):
    """The upload timeout is an in-memory timer, so a restart used to drop it
    and the row stayed awaiting_image forever, eating the user's open-session
    budget (12 such rows sat on prod since 2026-06-21). Startup must close the
    ones whose deadline elapsed while we were down."""
    Session = await _sm(tmp_path)
    now = datetime.now(timezone.utc)
    async with Session() as s:
        t = models.Task(task_uid="stale", user_id=1, workflow="creatives", status="awaiting_image")
        t.created_at = now - timedelta(hours=48)
        s.add(t)
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=24 * 3600)
    n = await svc.rearm_parked_timeouts()
    assert n == 1

    async with Session() as s:
        row = (await s.execute(select(models.Task))).scalar_one()
    assert row.status == "cancelled"
    assert row.error == "image_upload_timeout"
    assert row.finished_at is not None
    assert "stale" not in svc._timeouts  # closed, not waiting


@pytest.mark.asyncio
async def test_rearm_gives_fresh_parked_task_its_remaining_time(tmp_path):
    """A task parked minutes before the restart keeps waiting — the user may
    still come back and upload."""
    Session = await _sm(tmp_path)
    now = datetime.now(timezone.utc)
    async with Session() as s:
        t = models.Task(task_uid="fresh", user_id=1, workflow="creatives", status="awaiting_image")
        t.created_at = now - timedelta(minutes=5)
        s.add(t)
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=24 * 3600)
    n = await svc.rearm_parked_timeouts()
    assert n == 0  # nothing closed

    async with Session() as s:
        row = (await s.execute(select(models.Task))).scalar_one()
    assert row.status == "awaiting_image"
    assert "fresh" in svc._timeouts  # timer restored
    svc._cancel_timeout("fresh")


@pytest.mark.asyncio
async def test_rearm_closes_stale_awaiting_text_too(tmp_path):
    """The text park is the second door of the same leak: the user opened /new,
    got the 12 propositions and closed the tab. It used to have no deadline at
    all, so the row sat open forever and never reached usage stats."""
    Session = await _sm(tmp_path)
    async with Session() as s:
        t = models.Task(task_uid="txt", user_id=1, workflow="creatives", status="awaiting_text")
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=48)
        s.add(t)
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=24 * 3600)
    assert await svc.rearm_parked_timeouts() == 1

    async with Session() as s:
        row = (await s.execute(select(models.Task))).scalar_one()
    assert row.status == "cancelled"
    assert row.error == "text_approve_timeout"  # not the upload one
    assert "txt" not in svc._timeouts


@pytest.mark.asyncio
async def test_rearm_closes_stale_awaiting_persona_too(tmp_path):
    """Третья парковка (persona) течёт так же, как первые две: брошенная на
    экране «Кому пишем» сессия должна закрываться по дедлайну со своим кодом."""
    Session = await _sm(tmp_path)
    async with Session() as s:
        t = models.Task(task_uid="prs", user_id=1, workflow="creatives", status="awaiting_persona")
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=48)
        s.add(t)
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=24 * 3600)
    assert await svc.rearm_parked_timeouts() == 1

    async with Session() as s:
        row = (await s.execute(select(models.Task))).scalar_one()
    assert row.status == "cancelled"
    assert row.error == "persona_approve_timeout"
    assert "prs" not in svc._timeouts


@pytest.mark.asyncio
async def test_rearm_ignores_terminal_tasks(tmp_path):
    """Rows that already ended are none of the sweep's business."""
    Session = await _sm(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        for uid, status in (("fin", "done"), ("bad", "failed"), ("no", "cancelled")):
            t = models.Task(task_uid=uid, user_id=1, workflow="creatives", status=status)
            t.created_at = old
            s.add(t)
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=24 * 3600)
    assert await svc.rearm_parked_timeouts() == 0

    async with Session() as s:
        rows = {t.task_uid: t.status for t in (await s.execute(select(models.Task))).scalars()}
    assert rows == {"fin": "done", "bad": "failed", "no": "cancelled"}
    assert svc._timeouts == {}


@pytest.mark.asyncio
async def test_text_timeout_fires_when_still_parked(tmp_path):
    """Armed at the text park, the timer resumes with {action:timeout} — the
    HITL node turns that into a cancel with its own error marker."""
    Session = await _sm(tmp_path)
    async with Session() as s:
        s.add(models.Task(task_uid="tt", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=0)
    captured = {}

    async def fake_submit_decision(uid, user_id, decision):
        captured["decision"] = decision

    svc.submit_decision = fake_submit_decision  # type: ignore[assignment]
    svc._arm_park_timeout("tt", "awaiting_text")

    import asyncio

    await asyncio.sleep(0.05)
    assert captured.get("decision") == {"action": "timeout"}


@pytest.mark.asyncio
async def test_timeout_does_not_fire_at_the_other_park(tmp_path):
    """A timer armed for the text park must not fire on a task that has since
    moved to the image park — the move re-arms its own deadline."""
    Session = await _sm(tmp_path)
    async with Session() as s:
        s.add(models.Task(task_uid="mv", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    svc = _svc(Session, tmp_path, timeout_sec=0)
    fired = {}

    async def fake_submit_decision(uid, user_id, decision):
        fired["x"] = True

    svc.submit_decision = fake_submit_decision  # type: ignore[assignment]
    svc._arm_park_timeout("mv", "awaiting_text")

    import asyncio

    await asyncio.sleep(0.05)
    assert "x" not in fired


@pytest.mark.asyncio
async def test_image_timeout_skipped_if_no_longer_parked(tmp_path):
    from app.services.creatives import CreativesService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    Session = await _sm(tmp_path)
    async with Session() as s:
        s.add(models.Task(task_uid="tp", user_id=1, workflow="creatives", status="done"))
        await s.commit()

    svc = CreativesService(
        manager=TaskManager(tmp_root=tmp_path / "tmp"), bus=EventBus(),
        sessionmaker=Session, graph=object(), results_dir=tmp_path / "res",
        park_timeout_sec=0,
    )
    fired = {}

    async def fake_submit_decision(uid, user_id, decision):
        fired["x"] = True

    svc.submit_decision = fake_submit_decision  # type: ignore[assignment]
    svc._arm_park_timeout("tp", "awaiting_image")
    import asyncio

    await asyncio.sleep(0.05)
    assert "x" not in fired  # task already done → timeout must not fire
