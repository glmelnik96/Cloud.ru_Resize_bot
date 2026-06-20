"""In-process async task manager: per-user queue + worker + semaphores.

Ported from App1 (Telegram-free), trimmed for App3 v1: no recipe/regen cache
(A/B regen is deferred). The key App3 difference is HOW the runner uses
``global_sem``: a creatives task is a CHAIN of short segments glued by the
langgraph checkpoint, and each segment acquires ``global_sem`` only for its
compute stretch and releases it the moment the graph parks at an interrupt.
So ``max_concurrency`` caps simultaneously-COMPUTING tasks, not parked ones —
a task awaiting user input holds no global slot.
"""
from __future__ import annotations

import asyncio
import shutil
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable, Optional

import structlog

log = structlog.get_logger(__name__)

MAX_PER_USER_INFLIGHT = 2
USER_QUEUE_LIMIT = 5

TaskRunner = Callable[[], Awaitable[None]]


class TaskManager:
    def __init__(
        self,
        *,
        tmp_root: Path,
        max_concurrency: int = 5,
        max_per_user_inflight: int = MAX_PER_USER_INFLIGHT,
        user_queue_limit: int = USER_QUEUE_LIMIT,
    ) -> None:
        self.tmp_root = Path(tmp_root)
        self.global_sem = asyncio.Semaphore(max_concurrency)
        self.max_per_user_inflight = max_per_user_inflight
        self.user_queue_limit = user_queue_limit

        self.user_queues: dict[str, asyncio.Queue[TaskRunner]] = {}
        self.user_workers: dict[str, asyncio.Task] = {}
        self.user_sems: dict[str, asyncio.Semaphore] = {}
        self.user_inflight: Counter[str] = Counter()

        self.tmp_root.mkdir(parents=True, exist_ok=True)

    # ── tmp dirs ──────────────────────────────────────────────
    def task_tmp(self, user_id: str, task_uid: str) -> Path:
        d = self.tmp_root / str(user_id) / task_uid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def clear_task_tmp(self, user_id: str, task_uid: str) -> None:
        d = self.tmp_root / str(user_id) / task_uid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    # ── per-user lane ─────────────────────────────────────────
    def _ensure_user_lane(self, user_id: str) -> asyncio.Queue:
        if user_id not in self.user_queues:
            self.user_queues[user_id] = asyncio.Queue(maxsize=self.user_queue_limit)
            self.user_sems[user_id] = asyncio.Semaphore(self.max_per_user_inflight)
            self.user_workers[user_id] = asyncio.create_task(
                self._user_worker(user_id), name=f"user-worker-{user_id}"
            )
        return self.user_queues[user_id]

    async def _user_worker(self, user_id: str) -> None:
        q = self.user_queues[user_id]
        sem = self.user_sems[user_id]
        while True:
            runner = await q.get()
            await sem.acquire()
            asyncio.create_task(
                self._run_one(user_id, runner, sem, q), name=f"user-task-{user_id}"
            )

    async def _run_one(
        self, user_id: str, runner: TaskRunner, sem: asyncio.Semaphore, q: asyncio.Queue
    ) -> None:
        self.user_inflight[user_id] += 1
        try:
            await runner()
        except Exception as exc:  # noqa: BLE001 — one task must not kill the lane
            log.exception("user_task_crashed", user_id=user_id, error=str(exc))
        finally:
            self.user_inflight[user_id] -= 1
            if self.user_inflight[user_id] <= 0:
                self.user_inflight.pop(user_id, None)
            sem.release()
            q.task_done()

    def user_load(self, user_id: str) -> tuple[int, int]:
        inflight = self.user_inflight.get(user_id, 0)
        q = self.user_queues.get(user_id)
        queued = q.qsize() if q else 0
        return inflight, queued

    async def submit(self, user_id: str, runner: TaskRunner) -> bool:
        q = self._ensure_user_lane(user_id)
        try:
            q.put_nowait(runner)
            return True
        except asyncio.QueueFull:
            return False

    async def shutdown(self) -> None:
        for t in list(self.user_workers.values()):
            t.cancel()
