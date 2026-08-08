"""Startup reconciliation after a process restart.

The in-memory queue and the per-segment compute are lost on restart, so a task
caught mid-compute (queued / running) is orphaned → mark it failed. Tasks parked
at an interrupt (awaiting_persona / awaiting_text / awaiting_image) are NOT
touched: their state
lives in the Redis checkpoint and the browser rehydrates via /api/tasks/{uid}/
pending, so they remain resumable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import update

from app.db import models

log = structlog.get_logger(__name__)

_STUCK = ("queued", "running")
_REASON = "interrupted by restart"


async def reconcile_interrupted_tasks(sessionmaker) -> int:
    """Flip orphaned queued/running tasks to failed. awaiting_* survive."""
    async with sessionmaker() as s:
        res = await s.execute(
            update(models.Task)
            .where(models.Task.status.in_(_STUCK))
            .values(status="failed", error=_REASON, finished_at=datetime.now(timezone.utc))
        )
        await s.commit()
    n = res.rowcount or 0
    if n:
        log.info("reconcile_failed_orphans", count=n)
    return n
