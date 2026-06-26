"""Task routes: create a creatives task, list/get tasks.

Decision (HITL) and SSE routes are added in Phase 3+.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select

from app.api.schemas import CreateTaskIn, TaskOut, TextDecisionIn
from app.auth.deps import get_current_user
from app.db import models
from app.services.creatives import CapacityError

router = APIRouter(prefix="/api", tags=["tasks"])


def _task_images(t: models.Task, results_dir: Path | None) -> list[str]:
    """Banner PNGs of a finished run, derived from disk (self-healing: after
    the 24h retention purge the dir is gone → empty list, no broken links).
    Zero-padded names (01_photo, 02_render, …) sort into banner order; the ZIP
    is excluded."""
    if t.status != "done" or results_dir is None:
        return []
    task_dir = results_dir / t.task_uid
    if not task_dir.is_dir():
        return []
    return [f"/results/{t.task_uid}/{p.name}" for p in sorted(task_dir.glob("*.png"))]


_BRIEF_KEYS = ("product", "audience", "emotion")


def _task_brief(t: models.Task) -> dict[str, str]:
    """The original brief fields, whitelisted so no other internal params can
    leak into the response."""
    params = t.params or {}
    return {k: params[k] for k in _BRIEF_KEYS if k in params}


def _task_out(t: models.Task, results_dir: Path | None = None) -> TaskOut:
    return TaskOut(
        task_uid=t.task_uid,
        workflow=t.workflow,
        status=t.status,
        prompt=t.prompt,
        result_url=t.result_url,
        error=t.error,
        created_at=t.created_at.isoformat() if t.created_at else None,
        images=_task_images(t, results_dir),
        brief=_task_brief(t),
    )


def _results_dir(request: Request) -> Path | None:
    service = getattr(request.app.state, "creatives", None)
    return getattr(service, "results_dir", None)


@router.post("/tasks")
async def create_task(body: CreateTaskIn, request: Request):
    user = await get_current_user(request)
    service = getattr(request.app.state, "creatives", None)
    if service is None:
        raise HTTPException(503, "service unavailable (graph not initialised)")
    try:
        task_uid = await service.create(str(user.id), body.model_dump())
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"task_uid": task_uid}


@router.get("/tasks")
async def list_tasks(request: Request):
    user = await get_current_user(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        res = await s.execute(
            select(models.Task)
            .where(models.Task.user_id == user.id)
            .order_by(models.Task.id.desc())
            .limit(100)
        )
        results_dir = _results_dir(request)
        return [_task_out(t, results_dir) for t in res.scalars().all()]


@router.get("/tasks/{uid}")
async def get_task(uid: str, request: Request):
    user = await get_current_user(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == uid))
        task = res.scalar_one_or_none()
        if task is None or task.user_id != user.id:
            raise HTTPException(404, "task not found")
        return _task_out(task, _results_dir(request))


async def _load_owned(request: Request, uid: str, user) -> models.Task:
    Session = request.app.state.sessionmaker
    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == uid))
        task = res.scalar_one_or_none()
    if task is None or task.user_id != user.id:
        raise HTTPException(404, "task not found")
    return task


@router.get("/tasks/{uid}/pending")
async def task_pending(uid: str, request: Request):
    """Re-fetch the parked decision payload (reconnect rehydration)."""
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status not in ("awaiting_text", "awaiting_image"):
        return {"phase": None, "status": task.status}
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")
    payload = await service.pending(uid, task.status)
    return payload or {"phase": None, "status": task.status}


@router.post("/tasks/{uid}/decision/text")
async def decide_text(uid: str, body: TextDecisionIn, request: Request):
    """Resume HITL pause #1 (text approve)."""
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "awaiting_text":
        raise HTTPException(409, f"task not awaiting text (status={task.status})")
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")
    decision = {"action": body.action}
    await service.submit_decision(uid, str(user.id), decision)
    return {"ok": True, "action": body.action}


_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _safe_suffix(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return ext if ext in _IMAGE_EXT else ".png"


@router.post("/tasks/{uid}/decision/image")
async def decide_image(
    uid: str,
    request: Request,
    action: str = Form("upload"),
    file: UploadFile | None = File(None),
):
    """Resume HITL pause #2 (hero image).

    multipart form:
      - action=upload + file  → save the browser upload, resume with local_path
      - action=cancel         → cancel the task
      - action=generate       → web Phygital generation (Phase 5; 501 for now)
    """
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "awaiting_image":
        raise HTTPException(409, f"task not awaiting image (status={task.status})")
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")

    if action == "cancel":
        await service.submit_decision(uid, str(user.id), {"action": "cancel"})
        return {"ok": True, "action": "cancel"}

    if action == "generate":
        from app.services.hero_gen import HeroGenUnavailable

        try:
            await service.generate_decision(
                uid, str(user.id),
                end_user_id=user.gateway_user_id,
                end_user_email=user.email,
            )
        except HeroGenUnavailable as exc:
            raise HTTPException(501, str(exc)) from exc
        return {"ok": True, "action": "generate"}

    # upload
    if file is None:
        raise HTTPException(422, "no file provided for upload")
    manager = request.app.state.manager
    dest_dir = manager.task_tmp(str(user.id), uid)
    dest = Path(dest_dir) / f"hero{_safe_suffix(file.filename)}"
    data = await file.read()
    dest.write_bytes(data)
    decision = {"action": "upload", "local_path": str(dest)}
    await service.submit_decision(uid, str(user.id), decision)
    return {"ok": True, "action": "upload"}
