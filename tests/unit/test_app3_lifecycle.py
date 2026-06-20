"""App3 Phase 6 — reconcile-after-restart, retention, image-upload timeout."""
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
        image_timeout_sec=0,  # fire immediately
    )
    captured = {}

    async def fake_submit_decision(uid, user_id, decision):
        captured["decision"] = decision

    svc.submit_decision = fake_submit_decision  # type: ignore[assignment]
    svc._arm_image_timeout("to")
    # let the 0-sec sleeper run
    import asyncio

    await asyncio.sleep(0.05)
    assert captured.get("decision") == {"action": "timeout"}


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
        image_timeout_sec=0,
    )
    fired = {}

    async def fake_submit_decision(uid, user_id, decision):
        fired["x"] = True

    svc.submit_decision = fake_submit_decision  # type: ignore[assignment]
    svc._arm_image_timeout("tp")
    import asyncio

    await asyncio.sleep(0.05)
    assert "x" not in fired  # task already done → timeout must not fire
