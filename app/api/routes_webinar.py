"""Webinar resize routes (M4-web): one-shot form → compose loop → ZIP.

No HITL: the browser submits the variant, the texts, the hero file and
(optionally) the manual fit transform from the canvas engine in a single
multipart POST. Progress/done arrive over the existing SSE stream by
task_uid; the ZIP is served from /results/<uid>/.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.auth.deps import get_current_user
from app.services.webinar import CapacityError, _VARIANTS
from infra.hero_fit import Transform

router = APIRouter(prefix="/api/webinar", tags=["webinar"])


@router.get("/meta")
async def webinar_meta(request: Request):
    """Reference-frame geometry + text slots for the canvas fit engine."""
    await get_current_user(request)
    service = getattr(request.app.state, "webinar", None)
    out: dict[str, dict] = {}
    for name, v in _VARIANTS.items():
        slots: list[str] = []
        count = 0
        if service is not None:
            scenario = service.manifest.scenarios.get(v["scenario"])
            if scenario is not None:
                slots = list(scenario.slots)
                count = len(scenario.formats)
        out[name] = {
            "frame": list(v["frame"]),
            "box": list(v["box"]),
            "mode": v["mode"],
            "anchor_v": v["anchor_v"],
            "slots": slots,
            "formats": count,
        }
    return out


@router.post("/tasks")
async def create_webinar_task(
    request: Request,
    variant: str = Form(...),
    title: str = Form(""),
    subtitle: str = Form(""),
    date: str = Form(""),
    time: str = Form(""),
    fit_scale: float | None = Form(None),
    fit_x: float | None = Form(None),
    fit_y: float | None = Form(None),
    file: UploadFile = File(...),
):
    user = await get_current_user(request)
    service = getattr(request.app.state, "webinar", None)
    if service is None:
        raise HTTPException(503, "webinar service unavailable")

    fit = (fit_scale, fit_x, fit_y)
    if any(v is not None for v in fit) and not all(v is not None for v in fit):
        raise HTTPException(422, "fit_scale/fit_x/fit_y must be provided together")
    transform = Transform(scale=fit_scale, x=fit_x, y=fit_y) if fit_scale is not None else None
    if transform is not None and transform.scale <= 0:
        raise HTTPException(422, "fit_scale must be positive")

    hero_bytes = await file.read()
    if not hero_bytes:
        raise HTTPException(422, "empty hero file")

    fields = {"variant": variant, "title": title, "subtitle": subtitle,
              "date": date, "time": time}
    try:
        task_uid = await service.create(
            str(user.id), fields, hero_bytes=hero_bytes, transform=transform
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"task_uid": task_uid}
