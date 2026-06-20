"""Task routes: create a creatives task, list/get tasks.

Decision (HITL) and SSE routes are added in Phase 3+.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.schemas import CreateTaskIn, TaskOut
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
