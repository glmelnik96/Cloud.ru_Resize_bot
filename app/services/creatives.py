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
import contextlib
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog
from sqlalchemy import func, select, update

from app import usage
from app.db import models
from app.services.hero_gen import HeroGenerator, HeroGenUnavailable, NullHeroGenerator
from app.tasks.events import EventBus
from app.tasks.manager import TaskManager
from app.tasks.status import WebStatusReporter
from graph.builder import GRAPH_VERSION
from graph.state import AdBrief

log = structlog.get_logger(__name__)

# Per-node label for SSE "step" events (mirrors bot/graph_runner._NODE_LABELS).
_NODE_LABELS: dict[str, str] = {
    "understand_product": "Изучаю продукт",
    "derive_persona": "Готовлю персон ЦА",
    "generate_message_candidates": "Генерирую 24 предложения",
    "select_by_persona": "ЦА отбирает 12 лучших",
    "lint_candidates": "Проверяю формулировки на честность",
    "route_image_style": "Подбираю стиль картинки",
    "generate_image_prompt": "Пишу промпт для hero-картинки",
    "fill_templates_per_format": "Накладываю в шаблоны",
    "render_all": "Собираю ZIP",
}

# Non-terminal statuses count toward a user's open-session budget. App3 gates
# create() by a DB count of these (not TaskManager.has_capacity) because parked
# tasks legitimately sit open for a long time.
_OPEN_STATUSES = ("queued", "running", "awaiting_persona", "awaiting_text", "awaiting_image")

# The three HITL parks. All wait on a human and therefore all need a deadline —
# without one an abandoned session never ends and never reaches usage stats.
_PARKED_STATUSES = ("awaiting_persona", "awaiting_text", "awaiting_image")

# Error stamped on a task the deadline closed, per park. Matches what the HITL
# node itself returns when resumed with {action: timeout}.
_TIMEOUT_ERRORS = {
    "awaiting_persona": "persona_approve_timeout",
    "awaiting_text": "text_approve_timeout",
    "awaiting_image": "image_upload_timeout",
}

# Max concurrent hero generations against App1 during a single /new run. The
# 12 propositions each need a hero; this bounds the burst on App1's backend.
_HERO_GEN_CONCURRENCY = int(os.environ.get("HERO_GEN_CONCURRENCY", "4"))


class CapacityError(Exception):
    """Raised when a user already has too many open creatives tasks."""


class DecisionConflict(Exception):
    """Raised when a resume/generate loses the atomic claim on a parked task.

    Two POSTs racing the same awaiting_* task both pass the route's status
    check (a TOCTOU read); only the one that wins the compare-and-set flip to
    'running' proceeds. The loser raises this so the route answers 409 instead
    of resuming the graph a second time (which would corrupt the checkpoint)."""


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


def build_brief(fields: dict[str, str]) -> dict:
    """Wizard fields → AdBrief, verbatim.

    Until 2026-07-28 this concatenated the fields into one string that an LLM
    (``parse_brief``) then split back apart — a round trip that could only lose
    the marketer's wording. The fields are structured here, so the brief is
    built here.
    """
    return AdBrief(
        product=fields.get("product", ""),
        audience_raw=fields.get("audience", ""),
        emotion=fields.get("emotion", ""),
        notes=fields.get("notes", ""),
        source_url=fields.get("source_url", ""),
        product_slug=(fields.get("product_slug") or "auto"),
    ).model_dump()


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
        park_timeout_sec: int = 24 * 3600,
    ) -> None:
        self.manager = manager
        self.bus = bus
        self.Session = sessionmaker
        self.graph = graph
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.hero_generator: HeroGenerator = hero_generator or NullHeroGenerator()
        self.max_open_per_user = max_open_per_user
        self.park_timeout_sec = park_timeout_sec
        self._timeouts: dict[str, asyncio.Task] = {}
        # Per-user lock serializing create()'s open-count check → insert so two
        # concurrent submissions can't both slip past the cap (TOCTOU).
        self._create_locks: dict[str, asyncio.Lock] = {}

    def _user_lock(self, user_id: str) -> asyncio.Lock:
        """One asyncio.Lock per user id, created lazily. Safe without guarding
        because there is no await between the lookup and the insert (the event
        loop can't switch tasks mid-statement)."""
        lock = self._create_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._create_locks[user_id] = lock
        return lock

    # ── create ────────────────────────────────────────────────
    async def create(self, user_id: str, fields: dict[str, str]) -> str:
        """Persist a queued task, enqueue segment 1, return task_uid."""
        task_uid = uuid.uuid4().hex[:12]
        async with self._user_lock(str(user_id)):
            if await self._open_count(int(user_id)) >= self.max_open_per_user:
                raise CapacityError("too many open tasks")
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
            # Штамп топологии в чекпоинте: гард в submit_decision сравнит его
            # с текущей GRAPH_VERSION при резюме после деплоя.
            "graph_version": GRAPH_VERSION,
            "brief": build_brief(fields),
        }

        async def runner() -> None:
            await self._run_segment(
                task_uid, user_id, payload, reporter,
                first_label=_NODE_LABELS["understand_product"],
            )

        if not await self.manager.submit(user_id, runner):
            await reporter.error("queue full")
            await self._finish(
                task_uid, "failed", error="queue full", reason="queue_full"
            )
            raise CapacityError("queue full")
        return task_uid

    # ── resume (HITL decision) ────────────────────────────────
    async def submit_decision(self, task_uid: str, user_id: str, decision: dict) -> None:
        """Resume a parked graph with the user's decision as the next segment.

        Atomically claims the task (awaiting_* → running via a single
        compare-and-set UPDATE) so two POSTs racing the same parked task can't
        both resume the graph — the loser raises DecisionConflict (→ 409). The
        route's own status check is a TOCTOU read; this claim is the real guard.

        If the lane queue is full the decision must NOT be silently dropped:
        the parked status is restored (the graph checkpoint never moved) and
        CapacityError bubbles up so the route can answer 429 — the user just
        retries the same button.
        """
        from langgraph.types import Command

        prior = await self._claim_running(task_uid)
        if prior is None:
            raise DecisionConflict("task is not awaiting a decision")
        self._cancel_timeout(task_uid)
        reporter = WebStatusReporter(self.bus, task_uid=task_uid, label="creatives", eta_sec=None)

        # Гард топологии: чекпоинт, записанный другой версией графа (деплой
        # сменил набор узлов/рёбер), нельзя безопасно резюмировать — вместо
        # неопределённого поведения задача завершается честной ошибкой, и
        # пользователь просто перезапускает её (спека 2026-08-07).
        snapshot = await self.graph.aget_state(self._config(task_uid))
        stored = dict(snapshot.values or {}).get("graph_version")
        if stored != GRAPH_VERSION:
            log.warning("graph_version_mismatch", task_uid=task_uid, stored=stored)
            await reporter.error("пайплайн обновился — перезапустите задачу")
            await self._finish(
                task_uid, "failed",
                error="пайплайн обновился — перезапустите задачу",
                reason="graph_version",
            )
            return

        payload = Command(resume=decision)

        async def runner() -> None:
            await self._run_segment(
                task_uid, user_id, payload, reporter, first_label="Продолжаю"
            )

        if not await self.manager.submit(user_id, runner):
            restored = prior if prior in _PARKED_STATUSES else "awaiting_text"
            await self._set_status(task_uid, restored)
            self._arm_park_timeout(task_uid, restored)
            log.warning("decision_queue_full", task_uid=task_uid, restored=restored)
            raise CapacityError("queue full")

    async def generate_decision(
        self,
        task_uid: str,
        user_id: str,
        *,
        end_user_id: str | None = None,
        end_user_email: str | None = None,
    ) -> None:
        """Web-generate the 12 heroes (channel switch), then resume the upload.

        Pulls the per-proposition EN image_prompts + scenarios from the
        checkpoint and generates one hero per proposition via the hero
        generator (concurrently, bounded by _HERO_GEN_CONCURRENCY). Each hero
        carries its ranked ``index`` so a single failure never misaligns the
        banners. If at least one hero succeeds the graph resumes with the list;
        if ALL fail the task re-parks at awaiting_image for manual upload.

        ``end_user_id``/``end_user_email`` carry the GATEWAY identity of the end
        user (distinct from ``user_id``, the App3 lane key) so App1 can lane
        hero generation per end user (App1 commit 2010463); they are forwarded
        to the hero generator as X-User-* headers.

        Back-compat: a pre-redesign checkpoint without image_prompts/scenarios
        degrades to a single hero from image_prompt/image_style.
        """
        if not self.hero_generator.available:
            raise HeroGenUnavailable("hero generation backend not configured")

        prior = await self._claim_running(task_uid)
        if prior is None:
            raise DecisionConflict("task is not awaiting a decision")
        self._cancel_timeout(task_uid)
        reporter = WebStatusReporter(self.bus, task_uid=task_uid, label="creatives", eta_sec=None)

        async def runner() -> None:
            snapshot = await self.graph.aget_state(self._config(task_uid))
            values = dict(snapshot.values or {})
            prompts = values.get("image_prompts") or [values.get("image_prompt", "")]
            scenarios = values.get("scenarios") or [values.get("image_style", "render")]
            # pad scenarios to the prompts length (defensive)
            scenarios = [
                scenarios[i] if i < len(scenarios) else "photo"
                for i in range(len(prompts))
            ]
            tmp_dir = self.manager.task_tmp(user_id, task_uid)
            total = len(prompts)
            await reporter.start(f"Генерирую hero-картинки (0/{total})")

            sem = asyncio.Semaphore(_HERO_GEN_CONCURRENCY)
            done_count = 0

            async def _one(i: int, prompt: str, scenario: str) -> dict:
                nonlocal done_count
                dest = tmp_dir / f"hero_{i}.png"
                async with sem:
                    await self.hero_generator.generate(
                        prompt=prompt, style=scenario, dest=dest,
                        user_id=end_user_id, email=end_user_email,
                    )
                done_count += 1
                await reporter.step(f"Генерирую hero-картинки ({done_count}/{total})")
                return {
                    "url": None,
                    "local_path": str(dest),
                    "style": scenario,
                    "variant": "default",
                    "prompt": prompt,
                    "index": i,
                }

            async with self.manager.global_sem:
                results = await asyncio.gather(
                    *(_one(i, p, s) for i, (p, s) in enumerate(zip(prompts, scenarios))),
                    return_exceptions=True,
                )

            heroes = [r for r in results if isinstance(r, dict)]
            failures = [r for r in results if isinstance(r, BaseException)]
            if failures:
                log.warning(
                    "hero_gen_partial",
                    task_uid=task_uid,
                    ok=len(heroes),
                    failed=len(failures),
                    first_error=str(failures[0]),
                )

            if not heroes:
                await self._set_status(task_uid, "awaiting_image")
                await reporter.awaiting(
                    phase="image_upload",
                    data={
                        "image_prompt": prompts[0],
                        "image_style": scenarios[0],
                        "can_generate": self.hero_generator.available,
                        "gen_error": str(failures[0]) if failures else "no heroes",
                    },
                )
                return

            # heroes ready → resume the final segment as an upload
            from langgraph.types import Command

            await self._run_segment(
                task_uid, user_id,
                Command(resume={"action": "upload", "heroes": heroes}),
                reporter, first_label="Накладываю в шаблоны",
            )

        if not await self.manager.submit(user_id, runner):
            # Lane queue full — nothing was generated; restore the parked state
            # so the user keeps the generate/upload affordances and can retry.
            await self._set_status(task_uid, "awaiting_image")
            self._arm_park_timeout(task_uid, "awaiting_image")
            log.warning("generate_queue_full", task_uid=task_uid)
            raise CapacityError("queue full")

    async def pending(self, task_uid: str, status: str) -> Optional[dict]:
        """Re-fetch the parked interrupt payload from the checkpoint, so a
        reconnecting browser can re-render the decision UI."""
        snapshot = await self.graph.aget_state(self._config(task_uid))
        values = dict(snapshot.values or {})
        if status == "awaiting_persona":
            personas = values.get("personas") or []
            return {
                "phase": "persona_approve",
                "persona": personas[0] if personas else None,
                "kb_match": values.get("kb_match"),
            }
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
        except asyncio.CancelledError:
            # Lane/loop cancelled mid-compute — flip the row out of "running"
            # so it stops counting against the user's open-session budget
            # forever (a stuck "running" row is never terminal). Best-effort
            # and shielded because we may be in teardown; then re-raise so the
            # cancellation still propagates to the lane.
            log.warning("segment_cancelled", task_uid=task_uid)
            with contextlib.suppress(BaseException):
                await asyncio.shield(
                    self._finish(
                        task_uid, "failed", error="cancelled", reason="shutdown"
                    )
                )
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("segment_failed", task_uid=task_uid)
            await reporter.error(f"{type(exc).__name__}: {exc}")
            await self._finish(task_uid, "failed", error=str(exc), reason="error")
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
        if kind == "persona_approve":
            status = "awaiting_persona"
            await self._set_status(task_uid, status)
            data = {
                "persona": value.get("persona"),
                "kb_match": value.get("kb_match"),
            }
            await reporter.awaiting(phase="persona_approve", data=data)
        elif kind == "image_upload":
            status = "awaiting_image"
            await self._set_status(task_uid, status)
            data = {
                "image_prompt": value.get("image_prompt", ""),
                "image_style": value.get("image_style", ""),
                "can_generate": self.hero_generator.available,
            }
            await reporter.awaiting(phase="image_upload", data=data)
        else:
            status = "awaiting_text"
            await self._set_status(task_uid, status)
            data = {"candidates": value.get("candidates") if isinstance(value, dict) else []}
            await reporter.awaiting(phase="text_approve", data=data)
        self._arm_park_timeout(task_uid, status)
        log.info("task_parked", task_uid=task_uid, phase=kind or "text_approve")

    async def _finish_terminal(self, task_uid: str, reporter: WebStatusReporter, final: dict) -> None:
        # Graph reached END: user cancelled at an interrupt, or pipeline done.
        if final.get("cancelled"):
            reason = (
                "timeout"
                if final.get("error") in _TIMEOUT_ERRORS.values()
                else "user"
            )
            await self._finish(task_uid, "cancelled", reason=reason)
            await reporter.cancelled(reason=reason)
            log.info("task_cancelled", task_uid=task_uid, reason=reason)
            return

        result_url = self._collect_results(task_uid, final)
        cards = await self._collect_cards(task_uid)
        await reporter.done(result_url=result_url)
        meta = {"ratios": ["300x600"], "count": len(final.get("rendered_files") or [])}
        await self._finish(task_uid, "done", result_url=result_url, meta=meta, cards=cards)
        log.info("task_done", task_uid=task_uid, result_url=result_url)

    # Per-banner card fields surfaced on the final grid (Block 3, 2026-07-10).
    # Whitelist so internal candidate fields (id, …) never leak into params.
    _CARD_KEYS = ("slogan", "body", "cta", "hook_angle", "score", "reason")

    async def _collect_cards(self, task_uid: str) -> list[dict]:
        """Snapshot the ranked propositions (+ per-banner scenario) from the
        checkpoint. `final` only carries the LAST segment's updates (ranked was
        produced before the text HITL pause), so we read the full state.
        Best-effort: any failure → no cards, never a broken task."""
        try:
            snapshot = await self.graph.aget_state(self._config(task_uid))
            values = dict(snapshot.values or {})
        except Exception:  # noqa: BLE001
            log.warning("collect_cards_failed", task_uid=task_uid, exc_info=True)
            return []
        ranked = values.get("ranked") or []
        scenarios = values.get("scenarios") or []
        cards: list[dict] = []
        for i, cand in enumerate(ranked):
            if not isinstance(cand, dict):
                continue
            card = {k: cand[k] for k in self._CARD_KEYS if k in cand}
            card["scenario"] = scenarios[i] if i < len(scenarios) else ""
            cards.append(card)
        return cards

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

    # ── HITL park timeout ─────────────────────────────────────
    async def rearm_parked_timeouts(self) -> int:
        """Restore the park timeout for tasks left at awaiting_* by a restart.
        Returns how many were closed as expired.

        The timeout is an in-memory asyncio task, so a restart drops it and the
        row would sit parked forever — never purged (retention keeps open tasks
        regardless of age) and counting against the user's max_open_per_user
        budget for good.

        Deadline is measured from ``created_at``: parking happens seconds after
        creation while the timeout is hours, so the difference is noise.
        A task past its deadline is closed straight in the DB rather than by
        resuming the graph — the checkpoint may predate the current node set,
        and nobody is waiting on a resume that only leads to a cancel anyway.
        """
        now = datetime.now(timezone.utc)
        async with self.Session() as s:
            res = await s.execute(
                select(models.Task).where(models.Task.status.in_(_PARKED_STATUSES))
            )
            parked = list(res.scalars())

        expired = 0
        for task in parked:
            created = task.created_at or now
            if created.tzinfo is None:  # SQLite hands back naive UTC
                created = created.replace(tzinfo=timezone.utc)
            remaining = self.park_timeout_sec - (now - created).total_seconds()
            if remaining > 0:
                self._arm_park_timeout(task.task_uid, task.status, delay=remaining)
            else:
                await self._finish(
                    task.task_uid, "cancelled",
                    error=_TIMEOUT_ERRORS[task.status], reason="timeout",
                )
                expired += 1
        if parked:
            log.info(
                "parked_timeouts_rearmed",
                parked=len(parked),
                expired=expired,
            )
        return expired

    def _arm_park_timeout(
        self, task_uid: str, status: str, *, delay: float | None = None
    ) -> None:
        """Resume the graph with {action:timeout} if the user never comes back
        to the ``status`` park within park_timeout_sec (the HITL node then
        cancels the task).

        Every park needs this: an abandoned session at awaiting_text used to
        live forever exactly like the awaiting_image ones did, holding a slot of
        the user's open-session budget and never reaching usage stats at all.
        The persona park (awaiting_persona) leaks the same way.

        ``delay`` overrides the wait with the time left on an existing deadline
        (see rearm_parked_timeouts)."""
        self._cancel_timeout(task_uid)
        wait = self.park_timeout_sec if delay is None else delay

        async def _sleeper() -> None:
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                return
            # only fire if still parked where we armed it
            async with self.Session() as s:
                res = await s.execute(select(models.Task).where(models.Task.task_uid == task_uid))
                task = res.scalar_one_or_none()
            if task is None or task.status != status:
                return
            log.warning("park_timeout", task_uid=task_uid, status=status)
            try:
                await self.submit_decision(task_uid, str(task.user_id), {"action": "timeout"})
            except CapacityError:
                # Lane full at the exact timeout moment — submit_decision already
                # restored the park and re-armed the timeout; just log.
                log.warning("park_timeout_resume_deferred", task_uid=task_uid)
            except DecisionConflict:
                # A real decision won the claim between our status read and
                # here — the task already moved on; nothing to time out.
                log.info("park_timeout_superseded", task_uid=task_uid)

        try:
            self._timeouts[task_uid] = asyncio.create_task(_sleeper(), name=f"park-timeout-{task_uid}")
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

    async def _claim_running(self, task_uid: str) -> Optional[str]:
        """Atomically flip an awaiting_* task to 'running' (compare-and-set).

        Returns the prior awaiting status on success, or None if the row was
        not in an awaiting_* state — i.e. a concurrent resume already claimed
        it (or it's gone). The claim is a single ``UPDATE ... WHERE status =
        <prior>``: SQLite serializes writers, so of two racing resumes exactly
        one matches a row (rowcount 1) and the other matches none (rowcount 0)
        → the loser gets None and the caller rejects the duplicate."""
        async with self.Session() as s:
            res = await s.execute(
                select(models.Task).where(models.Task.task_uid == task_uid)
            )
            task = res.scalar_one_or_none()
            if task is None or task.status not in _PARKED_STATUSES:
                return None
            prior = task.status
            usage.close_work_window(task)
            usage.open_work_window(task)
            upd = await s.execute(
                update(models.Task)
                .where(models.Task.task_uid == task_uid)
                .where(models.Task.status == prior)
                .values(
                    status="running",
                    timings=task.timings,
                    started_at=func.coalesce(
                        models.Task.started_at, datetime.now(timezone.utc)
                    ),
                )
            )
            await s.commit()
            return prior if upd.rowcount else None

    async def _set_status(self, task_uid: str, status: str, *, started: bool = False) -> None:
        async with self.Session() as s:
            res = await s.execute(select(models.Task).where(models.Task.task_uid == task_uid))
            task = res.scalar_one_or_none()
            if task is None:
                return
            task.status = status
            # Close the previous compute window on every transition and open a
            # new one when we enter "running" — parking (awaiting_*) therefore
            # stops the clock, which is what keeps work_ms free of HITL waits.
            usage.close_work_window(task)
            if status == "running":
                usage.open_work_window(task)
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
        meta: Optional[dict[str, Any]] = None,
        cards: Optional[list[dict]] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Close the task and log exactly one usage row.

        ``reason`` says WHY a non-success ended (cross-app contract with App1,
        2026-08-02): ``user``/``timeout`` for cancelled, ``error``/``shutdown``/
        ``queue_full`` for failed. Without it a deploy that stopped a running
        task is indistinguishable from a broken pipeline.
        """
        async with self.Session() as s:
            res = await s.execute(select(models.Task).where(models.Task.task_uid == task_uid))
            task = res.scalar_one_or_none()
            if task is None:
                return
            task.status = status
            task.result_url = result_url
            task.error = error
            task.finished_at = datetime.now(timezone.utc)
            if cards:
                # reassign (not mutate) so the JSON column change is tracked
                task.params = {**(task.params or {}), "cards": cards}
            payload = {**(meta or {}), "work_ms": usage.close_work_window(task)}
            if reason:
                payload["reason"] = reason
            # Append-only usage log (one row per terminal status). Best-effort:
            # a logging failure must never break task completion.
            try:
                user = await s.get(models.User, task.user_id)
                await usage.log_creative_usage(
                    s, task=task, user=user, status=status, meta=payload
                )
            except Exception:  # noqa: BLE001
                log.warning("usage_log_failed", task_uid=task_uid, exc_info=True)
            await s.commit()
