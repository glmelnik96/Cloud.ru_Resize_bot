"""SSE route streaming task status events (verbatim from App1)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.auth.deps import get_current_user
from app.db import models

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/tasks/{uid}/events")
async def task_events(uid: str, request: Request, user=Depends(get_current_user)):
    # Ownership gate: without this any authenticated user could subscribe to
    # another user's stream (candidate slogans, prompts, result_url leak).
    Session = request.app.state.sessionmaker
    async with Session() as s:
        res = await s.execute(
            select(models.Task).where(models.Task.task_uid == uid)
        )
        task = res.scalar_one_or_none()
    if task is None or task.user_id != user.id:
        raise HTTPException(404, "task not found")

    bus = request.app.state.bus
    queue = bus.subscribe(uid)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if event.get("kind") == "_eof":
                    break
                yield {"event": event["kind"], "data": json.dumps(event)}
        finally:
            bus.unsubscribe(uid, queue)

    return EventSourceResponse(gen())
