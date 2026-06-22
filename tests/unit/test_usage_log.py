"""Usage-event logging: append-only analytics that survives task retention.

Mirrors the cross-app contract (docs platform repo
2026-06-22-usage-events-logging.md): one row per completed creatives
operation, identity from the gateway headers, meta carries only safe params
(no brief text / no full prompt / no files), and the retention purge must
never touch usage_events.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import models
from app.db.database import init_db, make_engine, make_sessionmaker
from app.tasks.retention import purge_once
from app.usage import log_creative_usage


@pytest.fixture
async def Session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def _seed_user_task(Session, *, status="done", started_offset_s=5.0):
    async with Session() as s:
        user = models.User(gateway_user_id="gw:1", yandex_id="y1", email="a@b.ru")
        s.add(user)
        await s.flush()
        now = datetime.now(timezone.utc)
        task = models.Task(
            task_uid="t1",
            user_id=user.id,
            workflow="creatives",
            prompt="secret brief: audience and emotion text",
            params={"product": "secret product", "audience": "secret audience"},
            status=status,
            created_at=now - timedelta(seconds=started_offset_s + 1),
            started_at=now - timedelta(seconds=started_offset_s),
            finished_at=now,
        )
        s.add(task)
        await s.commit()
        return user.id, task.id


async def test_log_creative_usage_records_metric(Session):
    uid, tid = await _seed_user_task(Session)
    async with Session() as s:
        user = await s.get(models.User, uid)
        task = await s.get(models.Task, tid)
        await log_creative_usage(
            s, task=task, user=user, status="done",
            meta={"ratios": ["300x600"], "count": 12},
        )
        await s.commit()

    async with Session() as s:
        ev = (await s.execute(select(models.UsageEvent))).scalar_one()
    assert ev.app == "creatives"
    assert ev.gateway_user_id == "gw:1"
    assert ev.email == "a@b.ru"
    assert ev.event == "variant"
    assert ev.workflow == "creatives"
    assert ev.status == "done"
    assert ev.duration_ms is not None and ev.duration_ms >= 4500
    assert ev.meta == {"ratios": ["300x600"], "count": 12}


async def test_log_creative_usage_omits_private_data(Session):
    """The brief (product/audience/emotion) and prompt must never leak into
    the usage log — only the explicit safe meta is stored."""
    uid, tid = await _seed_user_task(Session)
    async with Session() as s:
        user = await s.get(models.User, uid)
        task = await s.get(models.Task, tid)
        await log_creative_usage(
            s, task=task, user=user, status="done",
            meta={"ratios": ["300x600"], "count": 9},
        )
        await s.commit()

    async with Session() as s:
        ev = (await s.execute(select(models.UsageEvent))).scalar_one()
    assert "secret" not in str(ev.meta)


async def test_log_creative_usage_no_user_is_anonymous(Session):
    uid, tid = await _seed_user_task(Session)
    async with Session() as s:
        task = await s.get(models.Task, tid)
        await log_creative_usage(s, task=task, user=None, status="failed", meta={})
        await s.commit()

    async with Session() as s:
        ev = (await s.execute(select(models.UsageEvent))).scalar_one()
    assert ev.gateway_user_id is None
    assert ev.email == ""
    assert ev.status == "failed"
    assert ev.meta == {}


async def test_usage_event_survives_retention(Session, tmp_path: Path):
    uid, tid = await _seed_user_task(Session)
    async with Session() as s:
        user = await s.get(models.User, uid)
        task = await s.get(models.Task, tid)
        await log_creative_usage(
            s, task=task, user=user, status="done",
            meta={"ratios": ["300x600"], "count": 12},
        )
        await s.commit()

    # ttl_sec=0 → purge removes all terminal task rows but never usage_events.
    _dirs, rows = await purge_once(results_dir=tmp_path, sessionmaker=Session, ttl_sec=0)
    assert rows == 1
    async with Session() as s:
        tasks_left = (await s.execute(select(models.Task))).scalars().all()
        events_left = (await s.execute(select(models.UsageEvent))).scalars().all()
    assert tasks_left == []
    assert len(events_left) == 1
