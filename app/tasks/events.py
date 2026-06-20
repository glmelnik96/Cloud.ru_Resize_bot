"""In-memory pub/sub event bus keyed by task_uid (for SSE status streaming).

Ported verbatim from App1. In-process + per-process: events emitted while a
subscriber is disconnected are lost — the durable rehydration source is the
Task row + the langgraph checkpoint (see /api/tasks/{uid}/pending). App3 runs
single-worker for this reason.
"""
from __future__ import annotations

import asyncio
from typing import Any

Event = dict[str, Any]
DONE = {"kind": "_eof"}  # terminal sentinel for SSE loops


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue[Event]]] = {}

    def subscribe(self, task_uid: str) -> "asyncio.Queue[Event]":
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subs.setdefault(task_uid, []).append(q)
        return q

    def unsubscribe(self, task_uid: str, q: "asyncio.Queue[Event]") -> None:
        subs = self._subs.get(task_uid)
        if not subs:
            return
        if q in subs:
            subs.remove(q)
        if not subs:
            self._subs.pop(task_uid, None)

    async def publish(self, task_uid: str, event: Event) -> None:
        for q in list(self._subs.get(task_uid, [])):
            await q.put(event)

    async def close(self, task_uid: str) -> None:
        """Signal EOF to all subscribers of this task."""
        await self.publish(task_uid, DONE)
