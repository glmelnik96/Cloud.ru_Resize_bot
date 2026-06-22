"""Append-only usage log (see models.UsageEvent).

One row per completed creatives operation. Holds no images and no full prompt
— only the metric for analytics. Retention does not purge it. Cross-app
contract: docs platform repo 2026-06-22-usage-events-logging.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

APP_NAME = "creatives"


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


async def log_creative_usage(
    session: AsyncSession,
    *,
    task: "models.Task",
    user: "models.User | None",
    status: str,
    meta: Optional[dict[str, Any]] = None,
    app: str = APP_NAME,
) -> None:
    """Add one usage event to the open session (caller commits).

    `meta` must already be the anonymised, whitelisted payload (e.g.
    {"ratios": ["300x600"], "count": 12}) — the brief and prompt are never
    read here, so no private text can leak into the log.
    """
    session.add(
        models.UsageEvent(
            app=app,
            gateway_user_id=(user.gateway_user_id if user else None),
            email=(user.email if user else ""),
            event="variant",
            workflow=task.workflow,
            status=status,
            duration_ms=_duration_ms(task),
            meta=meta or {},
        )
    )
