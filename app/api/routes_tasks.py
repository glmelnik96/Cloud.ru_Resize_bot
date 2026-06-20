"""Task routes: create a creatives task, list/get tasks.

Decision (HITL) and SSE routes are added in Phase 3+.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.schemas import CreateTaskIn, TaskOut, TextDecisionIn
from app.auth.deps import get_current_user
from app.db import models
from app.services.creatives import CapacityError

router = APIRouter(prefix="/api", tags=["tasks"])


def _task_out(t: models.Task) -> TaskOut:
    return TaskOut(
        task_uid=t.task_uid,
        workflow=t.workflow,
        status=t.status,
        result_url=t.result_url,
        error=t.error,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


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
        return [_task_out(t) for t in res.scalars().all()]


@router.get("/tasks/{uid}")
async def get_task(uid: str, request: Request):
    user = await get_current_user(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == uid))
        task = res.scalar_one_or_none()
        if task is None or task.user_id != user.id:
            raise HTTPException(404, "task not found")
        return _task_out(task)


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
    decision = {"action": body.action, "comment": body.comment}
    await service.submit_decision(uid, str(user.id), decision)
    return {"ok": True, "action": body.action}
