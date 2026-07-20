"""Webinar resizes service — LangGraph-free compose loop (M4-web, 2026-07-21).

The webinar flow has no LLM and no HITL: one form (variant + texts) + one
hero upload (speaker photo or App1 metaphor render), optionally fitted by the
user in the web canvas (``infra.hero_fit.Transform``). The service bakes the
hero into the variant's reference frame once and renders every scenario format
via ``compose(hero_prefit=True)``, then zips the PNGs into results/<uid>/.

Why prefit works for both variants:
  - speaker formats use fit=cover boxes derived against the Figma reference
    speaker's visible-object window — the baked SPEAKER_FRAME window IS that
    object, so cover math reproduces the composition;
  - visual formats use file-space fit:crop percentages over a square file —
    the baked VISUAL_FRAME square is exactly such a file (crop path never
    bbox-crops anyway).
"""
from __future__ import annotations

import asyncio
import uuid
import zipfile
from pathlib import Path
from typing import Optional

import structlog
from PIL import Image
from sqlalchemy import func, select

from app import usage
from app.db import models
from app.tasks.events import EventBus
from app.tasks.manager import TaskManager
from app.tasks.status import WebStatusReporter
from infra.composer import compose
from infra.hero_fit import (
    SPEAKER_FRAME,
    VISUAL_BOX,
    VISUAL_FRAME,
    Transform,
    auto_transform,
    bake_frame,
)
from infra.template_manifest import TemplateManifest

log = structlog.get_logger(__name__)

# variant → (scenario key, reference frame, default placement box/mode/anchor)
_VARIANTS: dict[str, dict] = {
    "speaker": {
        "scenario": "webinar_speaker",
        "frame": SPEAKER_FRAME,
        "box": (0, 0, *SPEAKER_FRAME),
        "mode": "cover",
        "anchor_v": "top",
    },
    "visual": {
        "scenario": "webinar_visual",
        "frame": VISUAL_FRAME,
        "box": VISUAL_BOX,
        "mode": "contain",
        "anchor_v": "center",
    },
}

_TEXT_KEYS = ("title", "subtitle", "date", "time")

_OPEN_STATUSES = ("queued", "running")


class CapacityError(Exception):
    """Raised when a user already has too many open webinar tasks."""


def bake_hero(
    hero: Image.Image, variant: str, transform: Optional[Transform] = None
) -> Image.Image:
    """Bake the hero into the variant's reference frame.

    ``transform=None`` → server default (``auto_transform``): speaker covers
    the whole window anchored top (head up), visual object is contained in
    the reference metaphor box. An explicit transform (from the web fit
    engine) is baked verbatim.
    """
    try:
        v = _VARIANTS[variant]
    except KeyError:
        raise ValueError(f"unknown webinar variant: {variant!r}") from None
    if hero.mode != "RGBA":
        hero = hero.convert("RGBA")
    if transform is None:
        transform = auto_transform(
            hero, v["box"], mode=v["mode"], anchor_v=v["anchor_v"]
        )
    ref_w, ref_h = v["frame"]
    return bake_frame(hero, ref_w, ref_h, transform)


def render_webinar(
    manifest: TemplateManifest,
    variant: str,
    hero: Image.Image,
    texts: dict[str, str],
    *,
    assets_root: Path,
    out_dir: Path,
    transform: Optional[Transform] = None,
    formats: Optional[list[str]] = None,
) -> tuple[list[Path], Path]:
    """Render the scenario formats + ZIP; returns (png_paths, zip_path).

    Synchronous and CPU-bound (PIL) — the service runs it per-format via
    ``asyncio.to_thread`` to keep the event loop responsive.
    """
    scenario = manifest.scenarios[_VARIANTS[variant]["scenario"]]
    slugs = formats if formats is not None else list(scenario.formats)
    baked = bake_hero(hero, variant, transform)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for slug in slugs:
        spec = manifest.templates[slug]
        img = compose(
            spec,
            hero=baked,
            texts=texts,
            assets_root=assets_root,
            slug=slug,
            hero_prefit=True,
        )
        dest = out_dir / f"{slug}.png"
        img.save(dest)
        files.append(dest)

    zip_path = out_dir / f"webinar_{variant}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.name)
    return files, zip_path


class WebinarService:
    def __init__(
        self,
        *,
        manager: TaskManager,
        bus: EventBus,
        sessionmaker,
        manifest: TemplateManifest,
        assets_root: Path,
        results_dir: Path | str = "./data/results",
        max_open_per_user: int = 5,
    ) -> None:
        self.manager = manager
        self.bus = bus
        self.Session = sessionmaker
        self.manifest = manifest
        self.assets_root = Path(assets_root)
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.max_open_per_user = max_open_per_user

    async def create(
        self,
        user_id: str,
        fields: dict[str, str],
        *,
        hero_bytes: bytes,
        transform: Optional[Transform] = None,
    ) -> str:
        """Persist a queued task, enqueue the render, return task_uid."""
        variant = fields.get("variant", "")
        if variant not in _VARIANTS:
            raise ValueError(f"unknown webinar variant: {variant!r}")
        if await self._open_count(int(user_id)) >= self.max_open_per_user:
            raise CapacityError("too many open tasks")

        task_uid = uuid.uuid4().hex[:12]
        texts = {k: fields[k] for k in _TEXT_KEYS if fields.get(k)}
        async with self.Session() as s:
            s.add(
                models.Task(
                    task_uid=task_uid,
                    user_id=int(user_id),
                    workflow="webinar",
                    prompt=texts.get("title", ""),
                    params={"variant": variant, **texts},
                    status="queued",
                )
            )
            await s.commit()

        # Persist the hero into the task tmp dir before queuing, so the
        # runner never depends on the request lifetime.
        tmp_dir = self.manager.task_tmp(user_id, task_uid)
        hero_path = tmp_dir / "hero.png"
        hero_path.write_bytes(hero_bytes)

        reporter = WebStatusReporter(
            self.bus, task_uid=task_uid, label="webinar", eta_sec=None
        )
        _, queued = self.manager.user_load(user_id)
        await reporter.queued(queue_pos=queued + 1)

        async def runner() -> None:
            await self._run(task_uid, variant, hero_path, texts, transform, reporter)

        if not await self.manager.submit(user_id, runner):
            await reporter.error("queue full")
            await self._finish(task_uid, "failed", error="queue full")
            raise CapacityError("queue full")
        return task_uid

    async def _run(
        self,
        task_uid: str,
        variant: str,
        hero_path: Path,
        texts: dict[str, str],
        transform: Optional[Transform],
        reporter: WebStatusReporter,
    ) -> None:
        scenario = self.manifest.scenarios[_VARIANTS[variant]["scenario"]]
        slugs = list(scenario.formats)
        dest_dir = self.results_dir / task_uid
        await self._set_status(task_uid, "running")
        await reporter.start(f"Рендерю форматы (0/{len(slugs)})")
        try:
            hero = Image.open(hero_path).convert("RGBA")
            async with self.manager.global_sem:
                baked = bake_hero(hero, variant, transform)
                dest_dir.mkdir(parents=True, exist_ok=True)
                files: list[Path] = []
                for i, slug in enumerate(slugs, start=1):
                    spec = self.manifest.templates[slug]
                    img = await asyncio.to_thread(
                        compose,
                        spec,
                        hero=baked,
                        texts=texts,
                        assets_root=self.assets_root,
                        slug=slug,
                        hero_prefit=True,
                    )
                    dest = dest_dir / f"{slug}.png"
                    await asyncio.to_thread(img.save, dest)
                    files.append(dest)
                    await reporter.step(f"Рендерю форматы ({i}/{len(slugs)})")
                await reporter.step("Собираю ZIP")
                zip_path = dest_dir / f"webinar_{variant}.zip"

                def _zip() -> None:
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for f in files:
                            zf.write(f, f.name)

                await asyncio.to_thread(_zip)
        except Exception as exc:  # noqa: BLE001
            log.exception("webinar_render_failed", task_uid=task_uid)
            await reporter.error(f"{type(exc).__name__}: {exc}")
            await self._finish(task_uid, "failed", error=str(exc))
            return

        result_url = f"/results/{task_uid}/{zip_path.name}"
        await reporter.done(result_url=result_url)
        await self._finish(
            task_uid,
            "done",
            result_url=result_url,
            meta={"variant": variant, "count": len(files)},
        )
        log.info("webinar_done", task_uid=task_uid, count=len(files))

    # ── helpers (mirror CreativesService) ─────────────────────
    async def _open_count(self, user_id: int) -> int:
        async with self.Session() as s:
            res = await s.execute(
                select(func.count())
                .select_from(models.Task)
                .where(models.Task.user_id == user_id)
                .where(models.Task.workflow == "webinar")
                .where(models.Task.status.in_(_OPEN_STATUSES))
            )
            return int(res.scalar_one())

    async def _set_status(self, task_uid: str, status: str) -> None:
        from datetime import datetime, timezone

        async with self.Session() as s:
            res = await s.execute(
                select(models.Task).where(models.Task.task_uid == task_uid)
            )
            task = res.scalar_one_or_none()
            if task is None:
                return
            task.status = status
            if status == "running" and task.started_at is None:
                task.started_at = datetime.now(timezone.utc)
            await s.commit()

    async def _finish(
        self,
        task_uid: str,
        status: str,
        *,
        result_url: Optional[str] = None,
        error: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        from datetime import datetime, timezone

        async with self.Session() as s:
            res = await s.execute(
                select(models.Task).where(models.Task.task_uid == task_uid)
            )
            task = res.scalar_one_or_none()
            if task is None:
                return
            task.status = status
            task.result_url = result_url
            task.error = error
            task.finished_at = datetime.now(timezone.utc)
            try:
                user = await s.get(models.User, task.user_id)
                await usage.log_creative_usage(
                    s, task=task, user=user, status=status, meta=meta or {}
                )
            except Exception:  # noqa: BLE001
                log.warning("usage_log_failed", task_uid=task_uid, exc_info=True)
            await s.commit()
