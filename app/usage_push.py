"""Fire-and-forget push of a completed operation to the gateway's usage ingest.

Platform decision 2026-07-22 (variant B): each sub-app POSTs one event per
completed user operation (done/failed/cancelled) to the gateway, which folds
all sub-apps into a single /admin picture. Contract:

    POST <USAGE_INGEST_URL>          (e.g. https://cloudrudesign.ru/internal/usage)
    Content-Type: application/json
    X-Ingest-Token: <shared secret>
    {app, email, event="generation", workflow, status, duration_ms,
     gateway_user_id?, created_at?, meta?}  ->  200 {"ok": true}

Best-effort by design: a gateway outage or a non-2xx never affects task
completion — the local ``usage_events`` row is the source of truth. There is no
dedup on the gateway, so we send exactly once with a single quick retry only on
network failure (401/422 are terminal, not retried). Disabled (no-op) when the
url or token is unset — local/dev runs never emit. ``images`` is App1's key and
is rejected by the gateway (422); this app only ever sends
creatives/webinar.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_TIMEOUT = 3.0
_WORKFLOW_MAX = 32

# Keep a strong reference to in-flight tasks so the event loop can't GC a
# pending fire-and-forget coroutine before it runs.
_pending: set[asyncio.Task] = set()


def emit(
    *,
    app: str,
    email: str,
    workflow: Optional[str],
    status: str,
    duration_ms: Optional[int],
    gateway_user_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """Schedule a best-effort POST of one usage event. No-op when disabled or
    when there is no running loop (sync context)."""
    url = settings.usage_ingest_url
    token = settings.usage_ingest_token
    if not url or not token:
        return  # push disabled (local/dev)

    payload: dict[str, Any] = {
        "app": app,
        "email": email or "",
        "event": "generation",
        "status": status,
    }
    if workflow:
        payload["workflow"] = workflow[:_WORKFLOW_MAX]
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if gateway_user_id:
        payload["gateway_user_id"] = gateway_user_id
    if meta:
        payload["meta"] = meta

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop — nothing to schedule onto

    t = asyncio.create_task(_post(url, token, payload))
    _pending.add(t)
    t.add_done_callback(_pending.discard)


async def _post(url: str, token: str, payload: dict[str, Any]) -> None:
    import httpx

    headers = {"Content-Type": "application/json", "X-Ingest-Token": token}
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                return
            # 401 (bad token) / 422 (unknown app / bad body) are terminal —
            # a retry would just fail again. Log once and drop.
            log.warning(
                "usage_push_rejected",
                http_status=r.status_code,
                app=payload.get("app"),
            )
            return
        except Exception as exc:  # noqa: BLE001 — network hiccup: one retry
            if attempt == 2:
                log.warning(
                    "usage_push_failed", error=str(exc), app=payload.get("app")
                )
