"""Web status reporter — publishes task lifecycle events to the EventBus.

Based on App1's WebStatusReporter (per-step timing, no Telegram). Extended for
App3's interactive HITL with `awaiting`, `resumed`, and `cancelled` events so
the browser can render decision UIs between graph segments.
"""
from __future__ import annotations

import time
from typing import Any

from app.tasks.events import EventBus


class WebStatusReporter:
    def __init__(self, bus: EventBus, *, task_uid: str, label: str, eta_sec: int | None) -> None:
        self.bus = bus
        self.task_uid = task_uid
        self.label = label
        self.eta_sec = eta_sec

        self._run_started: float | None = None
        self._step_started: float | None = None
        self._sub: str = ""
        self._step_times: list[tuple[str, float]] = []

    async def queued(self, *, queue_pos: int) -> None:
        await self.bus.publish(
            self.task_uid,
            {"kind": "queued", "queue_pos": queue_pos, "eta_sec": self.eta_sec},
        )

    async def start(self, first_step: str) -> None:
        now = time.monotonic()
        self._run_started = now
        self._step_started = now
        self._sub = first_step
        await self.bus.publish(
            self.task_uid,
            {"kind": "start", "step": first_step, "eta_sec": self.eta_sec, "ts": time.time()},
        )

    async def step(self, name: str) -> None:
        now = time.monotonic()
        if self._step_started is not None and self._sub:
            self._step_times.append((self._sub, now - self._step_started))
        self._sub = name
        self._step_started = now
        await self.bus.publish(self.task_uid, {"kind": "step", "step": name, "ts": time.time()})

    # ── App3 interactive HITL ────────────────────────────────────
    async def awaiting(self, *, phase: str, data: dict[str, Any]) -> None:
        """Graph parked at an interrupt; browser must render a decision UI.
        phase ∈ {"text_approve", "image_upload"}. `data` carries the payload
        (candidate text / image_prompt) for the UI; it is also re-fetchable via
        /api/tasks/{uid}/pending after a reconnect."""
        event = {"kind": "awaiting_input", "phase": phase, **data}
        await self.bus.publish(self.task_uid, event)

    async def resumed(self, *, phase: str) -> None:
        await self.bus.publish(self.task_uid, {"kind": "resumed", "phase": phase})

    async def cancelled(self, *, reason: str) -> None:
        await self.bus.publish(self.task_uid, {"kind": "cancelled", "reason": reason})
        await self.bus.close(self.task_uid)

    # ── terminal ─────────────────────────────────────────────────
    async def done(self, *, result_url: str | None) -> None:
        now = time.monotonic()
        if self._step_started is not None and self._sub:
            self._step_times.append((self._sub, now - self._step_started))
        total = (now - self._run_started) if self._run_started is not None else 0.0
        await self.bus.publish(
            self.task_uid,
            {
                "kind": "done",
                "result_url": result_url,
                "total_sec": round(total, 1),
                "breakdown": [[n, round(d, 1)] for n, d in self._step_times],
            },
        )
        await self.bus.close(self.task_uid)

    async def error(self, message: str) -> None:
        await self.bus.publish(
            self.task_uid,
            {"kind": "error", "message": (message or "").strip(), "last_step": self._sub},
        )
        await self.bus.close(self.task_uid)
