"""Creatives orchestrator — the Telegram-free port of bot/graph_runner.

Drives the compiled /new LangGraph as a chain of short SEGMENTS glued by the
langgraph checkpoint (Redis). A segment runs from one parking point to the
next interrupt (or to the terminal), acquiring the global semaphore only while
computing and releasing it the moment the graph parks. Parked tasks (awaiting
user input) hold no concurrency slot.

Phase 2 scope: create() + the first segment, which always parks at
``hitl_text_approve`` (status → awaiting_text). Resume + image + finalize land
in later phases.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog
from sqlalchemy import func, select

from app.db import models
from app.services.hero_gen import HeroGenerator, HeroGenUnavailable, NullHeroGenerator
from app.tasks.events import EventBus
from app.tasks.manager import TaskManager
from app.tasks.status import WebStatusReporter

log = structlog.get_logger(__name__)

# Per-node label for SSE "step" events (mirrors bot/graph_runner._NODE_LABELS).
_NODE_LABELS: dict[str, str] = {
    "parse_brief": "Разбираю бриф",
    "derive_persona": "Готовлю персон ЦА",
    "generate_message_candidates": "Генерирую 12 предложений",
    "rank_candidates": "Ранжирую предложения по ЦА",
    "route_image_style": "Подбираю стиль картинки",
    "generate_image_prompt": "Пишу промпт для hero-картинки",
    "fill_templates_per_format": "Накладываю в шаблоны",
    "render_all": "Собираю ZIP",
}

# Non-terminal statuses count toward a user's open-session budget. App3 gates
# create() by a DB count of these (not TaskManager.has_capacity) because parked
# tasks legitimately sit open for a long time.
_OPEN_STATUSES = ("queued", "running", "awaiting_text", "awaiting_image")


class CapacityError(Exception):
    """Raised when a user already has too many open creatives tasks."""


async def init_graph(checkpoint_db: str):
    """Open AsyncSqliteSaver + compile the /new graph once at startup.

    Durable HITL (park/resume across restarts) is backed by SQLite — no Redis
    on the VM (platform decision 2026-06-21). The checkpointer DB is separate
    from the app's Task/User DB so a schema change in one never touches the
    other. Returns (compiled_graph, checkpointer_cm); cm must be __aexit__'d
    on shutdown.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from graph.builder import build_text_graph

    cm = AsyncSqliteSaver.from_conn_string(checkpoint_db)
    saver = await cm.__aenter__()
    await saver.setup()
    compiled = build_text_graph().compile(checkpointer=saver)
    log.info("creatives_graph_ready", checkpoint_db=checkpoint_db)
    return compiled, cm


def build_raw_brief(fields: dict[str, str]) -> str:
    """Serialize the wizard fields into the raw_brief text parse_brief expects.
    The brief is product + audience + emotion (the emotion/образ the offer must
    evoke, formula "[чувство] + [образ/ассоциация]"). Channel/formats/goal are
    inferred downstream (parse_brief), so only the three collected fields are
    emitted."""
    return (
        f"Продукт: {fields.get('product', '')}\n"
        f"ЦА: {fields.get('audience', '')}\n"
        f"Эмоция: {fields.get('emotion', '')}\n"
    )


class CreativesService:
    def __init__(
        self,
        *,
        manager: TaskManager,
        bus: EventBus,
        sessionmaker,
        graph,
        results_dir: Path | str = "./data/results",
        hero_generator: HeroGenerator | None = None,
        max_open_per_user: int = 5,
        image_timeout_sec: int = 24 * 3600,
    ) -> None:
        self.manager = manager
        self.bus = bus
        self.Session = sessionmaker
        self.graph = graph
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.hero_generator: HeroGenerator = hero_generator or NullHeroGenerator()
        self.max_open_per_user = max_open_per_user
        self.image_timeout_sec = image_timeout_sec
        self._timeouts: dict[str, asyncio.Task] = {}

    # ── create ────────────────────────────────────────────────
    async def create(self, user_id: str, fields: dict[str, str]) -> str:
        """Persist a queued task, enqueue segment 1, return task_uid."""
        if await self._open_count(int(user_id)) >= self.max_open_per_user:
            raise CapacityError("too many open tasks")

        task_uid = uuid.uuid4().hex[:12]
        async with self.Session() as s:
            s.add(
                models.Task(
                    task_uid=task_uid,
                    user_id=int(user_id),
                    workflow="creatives",
                    prompt=fields.get("product", ""),
                    params=dict(fields),
                    status="queued",
                )
            )
            await s.commit()

        reporter = WebStatusReporter(self.bus, task_uid=task_uid, label="creatives", eta_sec=None)
        _, queued = self.manager.user_load(user_id)
        await reporter.queued(queue_pos=queued + 1)

        payload = {
            "session_id": task_uid,
            "user_id": int(user_id),
            "raw_brief": build_raw_brief(fields),
        }

        async def runner() -> None:
            await self._run_segment(
                task_uid, user_id, payload, reporter,
                first_label=_NODE_LABELS["parse_brief"],
            )

        if not await self.manager.submit(user_id, runner):
            await reporter.error("queue full")
            await self._finish(task_uid, "failed", error="queue full")
            raise CapacityError("queue full")
        return task_uid

    # ── resume (HITL decision) ────────────────────────────────
    async def submit_decision(self, task_uid: str, user_id: str, decision: dict) -> None:
        """Resume a parked graph with the user's decision as the next segment.

        Flips status → running synchronously to close the double-submit window
        (a second POST then sees running, not awaiting_*, → 409 upstream).
        """
        from langgraph.types import Command

        self._cancel_timeout(task_uid)
        await self._set_status(task_uid, "running")
        reporter = WebStatusReporter(self.bus, task_uid=task_uid, label="creatives", eta_sec=None)
        payload = Command(resume=decision)

        async def runner() -> None:
            await self._run_segment(
                task_uid, user_id, payload, reporter, first_label="Продолжаю"
            )

        await self.manager.submit(user_id, runner)

    async def generate_decision(self, task_uid: str, user_id: str) -> None:
        """Web-generate the hero (channel switch), then resume the graph upload.

        Pulls the EN image_prompt/style from the checkpoint, runs the hero
        generator, saves the result, and resumes with {action:upload}. On
        generator failure the task re-parks at awaiting_image so the user can
        upload manually instead.
        """
        if not self.hero_generator.available:
            raise HeroGenUnavailable("hero generation backend not configured")

        self._cancel_timeout(task_uid)
        await self._set_status(task_uid, "running")
        reporter = WebStatusReporter(self.bus, task_uid=task_uid, label="creatives", eta_sec=None)

        async def runner() -> None:
            snapshot = await self.graph.aget_state(self._config(task_uid))
            values = dict(snapshot.values or {})
            prompt = values.get("image_prompt", "")
            style = values.get("image_style", "render")
            dest = self.manager.task_tmp(user_id, task_uid) / "hero_gen.png"
            await reporter.start("Генерирую hero-картинку")
            try:
                async with self.manager.global_sem:
                    await self.hero_generator.generate(prompt=prompt, style=style, dest=dest)
            except Exception as exc:  # noqa: BLE001 — fall back to manual upload
                log.warning("hero_gen_failed", task_uid=task_uid, error=str(exc))
                await self._set_status(task_uid, "awaiting_image")
                await reporter.awaiting(
                    phase="image_upload",
                    data={
                        "image_prompt": prompt,
                        "image_style": style,
                        "can_generate": self.hero_generator.available,
                        "gen_error": str(exc),
                    },
                )
                return
            # hero ready → resume the final segment as an upload
            from langgraph.types import Command

            await self._run_segment(
                task_uid, user_id,
                Command(resume={"action": "upload", "local_path": str(dest)}),
                reporter, first_label="Накладываю в шаблоны",
            )

        await self.manager.submit(user_id, runner)

    async def pending(self, task_uid: str, status: str) -> Optional[dict]:
        """Re-fetch the parked interrupt payload from the checkpoint, so a
        reconnecting browser can re-render the decision UI."""
        snapshot = await self.graph.aget_state(self._config(task_uid))
        values = dict(snapshot.values or {})
        if status == "awaiting_text":
            return {"phase": "text_approve", "candidates": values.get("ranked") or []}
        if status == "awaiting_image":
            return {
                "phase": "image_upload",
                "image_prompt": values.get("image_prompt", ""),
                "image_style": values.get("image_style", ""),
                "can_generate": True,
            }
        return None

    # ── segment driver ────────────────────────────────────────
    async def _run_segment(
        self,
        task_uid: str,
        user_id: str,
        payload: Any,
        reporter: WebStatusReporter,
        *,
        first_label: str = "Запуск",
    ) -> None:
        """Run one compute segment to the next interrupt or to the terminal.
        Holds global_sem only while computing; releases at park."""
        await self._set_status(task_uid, "running", started=True)
        await reporter.start(first_label)
        try:
            async with self.manager.global_sem:
                final = await self._stream(task_uid, payload, reporter)
        except Exception as exc:  # noqa: BLE001
            log.exception("segment_failed", task_uid=task_uid)
            await reporter.error(f"{type(exc).__name__}: {exc}")
            await self._finish(task_uid, "failed", error=str(exc))
            return

        interrupts = final.get("__interrupt__")
        if interrupts:
            value = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            await self._park(task_uid, reporter, value)
            return

        # Terminal handling (ZIP delivery) lands in Phase 4.
        await self._finish_terminal(task_uid, reporter, final)

    async def _stream(self, task_uid: str, payload: Any, reporter: WebStatusReporter) -> dict:
        """Stream the graph for one segment, feeding node names into SSE steps.
        Returns the accumulated update dict (+ '__interrupt__' if parked)."""
        final: dict = {}
        config = self._config(task_uid)
        async for chunk in self.graph.astream(payload, config=config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue
            if "__interrupt__" in chunk:
                final["__interrupt__"] = chunk["__interrupt__"]
                continue
            for node_name, update in chunk.items():
                if isinstance(update, dict):
                    final.update(update)
                label = _NODE_LABELS.get(node_name)
                if label:
                    await reporter.step(label)
        return final

    async def _park(self, task_uid: str, reporter: WebStatusReporter, value: Any) -> None:
        """Set the awaiting status + publish the decision payload for the UI."""
        kind = value.get("kind") if isinstance(value, dict) else None
        if kind == "image_upload":
            await self._set_status(task_uid, "awaiting_image")
            data = {
                "image_prompt": value.get("image_prompt", ""),
                "image_style": value.get("image_style", ""),
                "can_generate": self.hero_generator.available,
            }
            await reporter.awaiting(phase="image_upload", data=data)
            self._arm_image_timeout(task_uid)
        else:
            await self._set_status(task_uid, "awaiting_text")
            data = {"candidates": value.get("candidates") if isinstance(value, dict) else []}
            await reporter.awaiting(phase="text_approve", data=data)
        log.info("task_parked", task_uid=task_uid, phase=kind or "text_approve")

    async def _finish_terminal(self, task_uid: str, reporter: WebStatusReporter, final: dict) -> None:
        # Graph reached END: user cancelled at an interrupt, or pipeline done.
        if final.get("cancelled"):
            reason = "timeout" if final.get("error") == "image_upload_timeout" else "user"
            await self._finish(task_uid, "cancelled")
            await reporter.cancelled(reason=reason)
            log.info("task_cancelled", task_uid=task_uid, reason=reason)
            return

        result_url = self._collect_results(task_uid, final)
        await reporter.done(result_url=result_url)
        await self._finish(task_uid, "done", result_url=result_url)
        log.info("task_done", task_uid=task_uid, result_url=result_url)

    def _collect_results(self, task_uid: str, final: dict) -> Optional[str]:
        """Move the graph's per-format PNGs + ZIP into results/<uid>/ and return
        the prefix-relative URL of the ZIP (served via the gateway prefix).
        Files may be missing if the render path changed — copy what exists."""
        dest_dir = self.results_dir / task_uid
        dest_dir.mkdir(parents=True, exist_ok=True)
        zip_url: Optional[str] = None

        zip_path = final.get("rendered_zip_path")
        if zip_path and Path(zip_path).exists():
            dest = dest_dir / f"{task_uid}.zip"
            shutil.copy(zip_path, dest)
            zip_url = f"/results/{task_uid}/{task_uid}.zip"

        for rec in final.get("rendered_files") or []:
            src = rec.get("path")
            fmt = rec.get("format", "format")
            if src and Path(src).exists():
                ext = Path(src).suffix or ".png"
                shutil.copy(src, dest_dir / f"{fmt}{ext}")

        # No ZIP but PNGs exist → point at the dir listing fallback (first PNG).
        if zip_url is None:
            pngs = sorted(dest_dir.glob("*.png"))
            if pngs:
                zip_url = f"/results/{task_uid}/{pngs[0].name}"
        return zip_url

    # ── image-upload timeout ──────────────────────────────────
    def _arm_image_timeout(self, task_uid: str) -> None:
        """Resume the graph with {action:timeout} if the user never provides a
        hero within image_timeout_sec (graph then cancels the task)."""
        self._cancel_timeout(task_uid)

        async def _sleeper() -> None:
            try:
                await asyncio.sleep(self.image_timeout_sec)
            except asyncio.CancelledError:
                return
            # only fire if still parked at awaiting_image
            async with self.Session() as s:
                res = await s.execute(select(models.Task).where(models.Task.task_uid == task_uid))
                task = res.scalar_one_or_none()
            if task is None or task.status != "awaiting_image":
                return
            log.warning("image_upload_timeout", task_uid=task_uid)
            await self.submit_decision(task_uid, str(task.user_id), {"action": "timeout"})

        try:
            self._timeouts[task_uid] = asyncio.create_task(_sleeper(), name=f"img-timeout-{task_uid}")
        except RuntimeError:
            pass  # no running loop (unit context without scheduling)

    def _cancel_timeout(self, task_uid: str) -> None:
        t = self._timeouts.pop(task_uid, None)
        if t is not None and not t.done():
            t.cancel()

    # ── helpers ───────────────────────────────────────────────
    def _config(self, task_uid: str) -> dict:
        return {"configurable": {"thread_id": task_uid}, "recursion_limit": 30}

    async def _open_count(self, user_id: int) -> int:
        async with self.Session() as s:
            res = await s.execute(
                select(func.count())
                .select_from(models.Task)
                .where(models.Task.user_id == user_id)
                .where(models.Task.status.in_(_OPEN_STATUSES))
            )
            return int(res.scalar_one())

    async def _set_status(self, task_uid: str, status: str, *, started: bool = False) -> None:
        async with self.Session() as s:
            res = await s.execute(select(models.Task).where(models.Task.task_uid == task_uid))
            task = res.scalar_one_or_none()
            if task is None:
                return
            task.status = status
            if started and task.started_at is None:
                task.started_at = datetime.now(timezone.utc)
            await s.commit()

    async def _finish(
        self,
        task_uid: str,
        status: str,
        *,
        result_url: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        async with self.Session() as s:
            res = await s.execute(select(models.Task).where(models.Task.task_uid == task_uid))
            task = res.scalar_one_or_none()
            if task is None:
                return
            task.status = status
            task.result_url = result_url
            task.error = error
            task.finished_at = datetime.now(timezone.utc)
            await s.commit()
