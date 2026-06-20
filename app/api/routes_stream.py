"""SSE route streaming task status events (verbatim from App1)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.auth.deps import get_current_user

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/tasks/{uid}/events")
async def task_events(uid: str, request: Request, user=Depends(get_current_user)):
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
