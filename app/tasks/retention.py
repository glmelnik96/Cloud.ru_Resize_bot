"""Retention purge: drop result dirs and Task rows older than the TTL.

App3 stores results as results/<uid>/ directories (per-format PNGs + ZIP), so
the purge is directory-aware. A still-parked task (awaiting_*) is never purged
by age — only terminal tasks' artifacts expire.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from sqlalchemy import delete

from app.db import models

log = structlog.get_logger(__name__)

DEFAULT_TTL_SEC = 24 * 3600
DEFAULT_INTERVAL_SEC = 10 * 60
_OPEN = ("queued", "running", "awaiting_text", "awaiting_image")


async def purge_once(*, results_dir: Path, sessionmaker, ttl_sec: int) -> tuple[int, int]:
    """Remove result dirs (by mtime) and terminal Task rows (by created_at)
    older than ttl_sec. Open (parked/running) tasks are kept regardless of age.
    Returns (dirs_removed, rows_removed)."""
    results_dir = Path(results_dir)
    dirs_removed = 0
    cutoff = time.time() - ttl_sec
    if results_dir.exists():
        for d in results_dir.iterdir():
            if not d.is_dir():
                continue
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    dirs_removed += 1
            except OSError as exc:
                log.warning("retention_rmdir_failed", path=str(d), error=str(exc))

    row_cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_sec)
    async with sessionmaker() as s:
        res = await s.execute(
            delete(models.Task)
            .where(models.Task.created_at < row_cutoff)
            .where(models.Task.status.not_in(_OPEN))
        )
        await s.commit()
    rows_removed = res.rowcount or 0
    if dirs_removed or rows_removed:
        log.info("retention_purged", dirs=dirs_removed, rows=rows_removed)
    return dirs_removed, rows_removed


async def retention_loop(
    *,
    results_dir: Path,
    sessionmaker,
    ttl_sec: int = DEFAULT_TTL_SEC,
    interval_sec: int = DEFAULT_INTERVAL_SEC,
) -> None:
    try:
        while True:
            await asyncio.sleep(interval_sec)
            try:
                await purge_once(results_dir=results_dir, sessionmaker=sessionmaker, ttl_sec=ttl_sec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("retention_iteration_failed", error=str(exc))
    except asyncio.CancelledError:
        raise
