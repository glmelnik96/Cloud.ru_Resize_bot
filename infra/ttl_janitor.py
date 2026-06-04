"""Background TTL janitor for generated artifact dirs.

Scope:
  After a /new run the bot has sent the hero PNG (HITL approval) and the final
  ZIP to TG — those messages are the durable copy. The local files in
  /data/images, /data/renders, /data/zips are scratch and can be purged.
  Without a janitor the named volumes grow unbounded (one ZIP per /new + N
  per-format PNGs + one hero PNG each session).

Why mtime, not parse-from-filename:
  Filenames carry timestamps today (e.g. `..._20260604T074304.png`), but those
  are operator hints, not the source of truth. mtime survives renames, doesn't
  depend on the producer's clock, and is the obvious primitive for «cleanup
  what's old».

Not touched:
  - /data/traces — RotatingFileHandler already caps size; debug history.
  - /data/phygital_storage — session.json MUST persist (SuperTokens refresh).
  - /data/user_data — Playwright profile (unused so far but reserved).

Tunables (env):
  - ARTIFACT_TTL_HOURS (default 24) — delete files older than this.
  - ARTIFACT_SWEEP_INTERVAL_S (default 3600) — how often the loop runs.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Dirs the janitor manages. Kept as a module constant so tests can patch.
JANITOR_DIRS: tuple[Path, ...] = (
    Path("/data/images"),
    Path("/data/renders"),
    Path("/data/zips"),
)

_TASK: asyncio.Task | None = None


def _ttl_hours() -> float:
    raw = os.environ.get("ARTIFACT_TTL_HOURS", "24")
    try:
        v = float(raw)
        return v if v > 0 else 24.0
    except ValueError:
        return 24.0


def _sweep_interval_s() -> float:
    raw = os.environ.get("ARTIFACT_SWEEP_INTERVAL_S", "3600")
    try:
        v = float(raw)
        return v if v > 0 else 3600.0
    except ValueError:
        return 3600.0


def sweep_once(*, ttl_s: float, now: float | None = None) -> dict[str, int]:
    """One pass over every JANITOR_DIRS entry, deleting files with
    `now - mtime > ttl_s`. Returns per-dir delete counts. Pure-ish (touches
    filesystem only) so unit tests can call it directly with a fake `now`.
    """
    if now is None:
        now = time.time()
    counts: dict[str, int] = {}
    for d in JANITOR_DIRS:
        deleted = 0
        if not d.exists():
            counts[d.name] = 0
            continue
        for p in d.iterdir():
            if not p.is_file():
                continue
            try:
                age = now - p.stat().st_mtime
            except OSError as exc:
                log.warning("ttl_janitor_stat_failed", path=str(p), error=str(exc))
                continue
            if age > ttl_s:
                try:
                    p.unlink()
                    deleted += 1
                except OSError as exc:
                    log.warning("ttl_janitor_unlink_failed", path=str(p), error=str(exc))
        counts[d.name] = deleted
    return counts


async def _run_loop() -> None:
    interval = _sweep_interval_s()
    ttl_s = _ttl_hours() * 3600.0
    log.info(
        "ttl_janitor_started",
        ttl_hours=round(ttl_s / 3600.0, 2),
        interval_s=interval,
        dirs=[str(d) for d in JANITOR_DIRS],
    )
    try:
        while True:
            try:
                counts = sweep_once(ttl_s=ttl_s)
                total = sum(counts.values())
                if total > 0:
                    log.info("ttl_janitor_swept", deleted_total=total, **counts)
                else:
                    # Quiet path — debug-level so we don't spam INFO every hour.
                    log.debug("ttl_janitor_swept_clean", **counts)
            except Exception as exc:  # noqa: BLE001 — loop must never die silently
                log.warning(
                    "ttl_janitor_sweep_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("ttl_janitor_stopped")
        raise


def start_ttl_janitor() -> asyncio.Task:
    """Spawn the periodic sweep task. Idempotent — returns the existing task
    if one is already running. Caller should hold the returned reference (we
    also cache it module-locally for `stop_ttl_janitor`)."""
    global _TASK
    if _TASK is not None and not _TASK.done():
        return _TASK
    _TASK = asyncio.create_task(_run_loop(), name="ttl_janitor")
    return _TASK


async def stop_ttl_janitor() -> None:
    """Cancel the sweep task and await its teardown. Safe to call when never
    started."""
    global _TASK
    task = _TASK
    _TASK = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
