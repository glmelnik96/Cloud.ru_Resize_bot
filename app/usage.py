"""Append-only usage log (see models.UsageEvent).

One row per completed creatives operation. Holds no images and no full prompt
— only the metric for analytics. Retention does not purge it. Cross-app
contract: docs platform repo 2026-06-22-usage-events-logging.md.

From 2026-07-22 to 2026-08-02 this also pushed each event to the gateway's
unified ingest. The platform retired that pooled counter — every tool gets its
own admin block now — so the push is gone and this table is the only place App3
records usage. The gateway reads it read-only (same host, same user).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models

APP_NAME = "creatives"


def _excluded_emails() -> set[str]:
    """Technical/smoke accounts kept out of usage entirely (local + push),
    matching App1. Parsed from settings each call so a config change needs no
    module reload."""
    raw = settings.usage_excluded_emails or ""
    return {e.strip() for e in raw.split(",") if e.strip()}


def _duration_ms(task: "models.Task") -> int | None:
    end = task.finished_at or datetime.now(timezone.utc)
    start = task.started_at or task.created_at
    if start is None:
        return None
    # SQLite returns naive UTC — normalise so aware/naive subtraction never raises.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - start).total_seconds() * 1000))


# ── compute-time accounting ──────────────────────────────────────
# `duration_ms` is wall-clock and therefore includes the HITL pauses: a build
# the user approved next morning honestly reports 15 hours. `work_ms` is the
# time the task actually held a compute slot — the sum of its "running"
# windows. Kept in the (previously unused) Task.timings JSON so no column has
# to be added to a live prod DB. Queue wait for the global compute slot counts
# as work: that is contention, not a human pause.
_WORK_KEY = "work_ms"
_WINDOW_KEY = "running_since"


def open_work_window(task: "models.Task", *, now: datetime | None = None) -> None:
    """Mark the task as computing from now on."""
    at = now or datetime.now(timezone.utc)
    task.timings = {**(task.timings or {}), _WINDOW_KEY: at.isoformat()}


def close_work_window(task: "models.Task", *, now: datetime | None = None) -> int:
    """Fold the open compute window into the accumulator; return total work ms.

    Idempotent: with no window open it just reports the accumulated total, so
    terminal paths that never ran (queue full at create) report 0."""
    at = now or datetime.now(timezone.utc)
    timings = {**(task.timings or {})}
    total = int(timings.get(_WORK_KEY) or 0)
    started = timings.pop(_WINDOW_KEY, None)
    if started:
        try:
            since = datetime.fromisoformat(started)
        except ValueError:
            since = None
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            total += max(0, int((at - since).total_seconds() * 1000))
    timings[_WORK_KEY] = total
    task.timings = timings
    return total


async def log_creative_usage(
    session: AsyncSession,
    *,
    task: "models.Task",
    user: "models.User | None",
    status: str,
    meta: Optional[dict[str, Any]] = None,
    app: str = APP_NAME,
    workflow: str | None = None,
) -> None:
    """Add one usage event to the open session (caller commits).

    `meta` must already be the anonymised, whitelisted payload (e.g.
    {"ratios": ["300x600"], "count": 12}) — the brief and prompt are never
    read here, so no private text can leak into the log.

    `workflow` overrides ``task.workflow`` for the logged value (webinar passes
    its variant so the speaker/visual split shows up in the stats). Smoke/tech
    accounts are dropped entirely — no row — matching App1.
    """
    if user is not None and user.email in _excluded_emails():
        return

    wf = workflow or task.workflow
    duration_ms = _duration_ms(task)
    session.add(
        models.UsageEvent(
            app=app,
            gateway_user_id=(user.gateway_user_id if user else None),
            email=(user.email if user else ""),
            event="variant",
            workflow=wf,
            status=status,
            duration_ms=duration_ms,
            meta=meta or {},
        )
    )
