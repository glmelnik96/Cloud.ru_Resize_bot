"""Task routes: create a creatives task, list/get tasks.

Decision (HITL) and SSE routes are added in Phase 3+.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.schemas import CreateTaskIn, PersonaDecisionIn, TaskOut, TextDecisionIn
from app.api.uploads import read_image_upload
from app.auth.deps import get_current_user
from app.config import settings
from app.db import models
from app.kb.store import latest_rows
from app.services.creatives import _PARKED_STATUSES, CapacityError, DecisionConflict

router = APIRouter(prefix="/api", tags=["tasks"])

# Result artifacts live at the root path "/results/<uid>/<file>" (the URL shape
# baked into result_url and the banner image links). Served through an
# ownership-gated route — NOT a bare StaticFiles mount, which would let any
# authenticated user read another user's ZIP/banners by uid (IDOR).
results_router = APIRouter(tags=["results"])


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


_BRIEF_KEYS = ("product", "audience", "emotion", "notes", "source_url", "product_slug")

# Служебные значения product_slug, которые не обязаны существовать в каталоге.
_SLUG_SENTINELS = ("auto", "none")


def _task_brief(t: models.Task) -> dict[str, str]:
    """The original brief fields, whitelisted so no other internal params can
    leak into the response."""
    params = t.params or {}
    return {k: params[k] for k in _BRIEF_KEYS if k in params}


_CARD_KEYS = ("scenario", "slogan", "body", "cta", "hook_angle", "score", "reason")


def _task_cards(t: models.Task) -> list[dict]:
    """Per-banner ranked cards persisted at 'done' (Block 3, 2026-07-10),
    whitelisted so internal candidate fields never leak."""
    cards = (t.params or {}).get("cards")
    if not isinstance(cards, list):
        return []
    return [
        {k: c[k] for k in _CARD_KEYS if k in c}
        for c in cards
        if isinstance(c, dict)
    ]


_RECIPE_KEYS = (
    "kb_source", "persona_segment", "winner_id", "slogan", "anchor",
    "desired_outcome", "metaphor", "intended_inference", "anti_reading",
    "metaphor_comments", "hero_source",
)


def _task_recipe(t: models.Task) -> dict:
    """Снимок решений запуска, whitelist — как у карточек."""
    recipe = (t.params or {}).get("recipe")
    if not isinstance(recipe, dict):
        return {}
    return {k: recipe[k] for k in _RECIPE_KEYS if k in recipe}


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
        cards=_task_cards(t),
        recipe=_task_recipe(t),
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
    slug = body.product_slug or "auto"
    if slug not in _SLUG_SENTINELS:
        # latest_rows без include_archived отдаёт только живые карточки:
        # архивная — это «больше не используем», запуск по ней отклоняем.
        rows = await latest_rows(request.app.state.sessionmaker)
        if slug not in {r.slug for r in rows}:
            raise HTTPException(422, f"unknown or archived product_slug: {slug}")
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


@results_router.get("/results/{uid}/{filename}")
async def get_result_file(uid: str, filename: str, request: Request):
    """Serve a finished task's artifact (ZIP/banner PNG), gated by ownership.

    Replaces an open StaticFiles mount that leaked any task's output to any
    authenticated user by uid. ``filename`` is confined to the task's own
    results dir (``resolve()`` + parent check blocks ``../`` traversal)."""
    user = await get_current_user(request)
    await _load_owned(request, uid, user)  # 404 unless the caller owns the task
    results_dir = getattr(request.app.state, "results_dir", None)
    if results_dir is None:
        raise HTTPException(404, "not found")
    task_dir = (Path(results_dir) / uid).resolve()
    target = (task_dir / filename).resolve()
    if target.parent != task_dir or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@router.get("/tasks/{uid}/pending")
async def task_pending(uid: str, request: Request):
    """Re-fetch the parked decision payload (reconnect rehydration)."""
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status not in _PARKED_STATUSES:
        return {"phase": None, "status": task.status}
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")
    payload = await service.pending(uid, task.status)
    return payload or {"phase": None, "status": task.status}


@router.post("/tasks/{uid}/decision/persona")
async def decide_persona(uid: str, body: PersonaDecisionIn, request: Request):
    """Resume остановки «Кому пишем» (первая пауза HITL)."""
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "awaiting_persona":
        raise HTTPException(409, f"task not awaiting persona (status={task.status})")
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")
    decision: dict = {"action": body.action}
    if body.action == "approve" and body.persona is not None:
        decision["persona"] = body.persona.model_dump()
    try:
        await service.submit_decision(uid, str(user.id), decision)
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"ok": True, "action": body.action}


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
    decision: dict = {"action": body.action}
    # winner_id имеет смысл только при approve: при regenerate набор
    # выбрасывается целиком, и указатель на карточку из него — мусор.
    if body.action == "approve" and body.winner_id:
        decision["winner_id"] = body.winner_id
    try:
        await service.submit_decision(uid, str(user.id), decision)
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"ok": True, "action": body.action}


_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _safe_suffix(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return ext if ext in _IMAGE_EXT else ".png"


_IMAGE_ACTIONS = ("upload", "generate", "cancel", "metaphor")

# Комментарий уходит в промпт LLM и копится в чекпоинте от итерации к итерации,
# поэтому у него есть потолок — как у notes/source_url в брифе.
_MAX_COMMENT_CHARS = 2000


@router.post("/tasks/{uid}/decision/image")
async def decide_image(
    uid: str,
    request: Request,
    action: str = Form("upload"),
    comment: str = Form(""),
    file: UploadFile | None = File(None),
):
    """Resume остановки «Картинка».

    multipart form:
      - action=upload + file    → сохранить загрузку, резюмить с local_path
      - action=generate         → серверная генерация 12 hero
      - action=metaphor + comment → перегенерация образа победителя (1 вызов LLM)
      - action=cancel           → отмена задачи
    """
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "awaiting_image":
        raise HTTPException(409, f"task not awaiting image (status={task.status})")
    if action not in _IMAGE_ACTIONS:
        raise HTTPException(422, f"unknown action: {action}")
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")

    if action == "metaphor":
        text = comment.strip()
        if not text:
            # Пустой комментарий не несёт правки — граф вернулся бы на ту же
            # остановку впустую, потратив вызов LLM.
            raise HTTPException(422, "metaphor comment is empty")
        if len(text) > _MAX_COMMENT_CHARS:
            raise HTTPException(
                422, f"metaphor comment too long (max {_MAX_COMMENT_CHARS})"
            )
        try:
            await service.submit_decision(
                uid, str(user.id), {"action": "metaphor", "comment": text}
            )
        except DecisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except CapacityError as exc:
            raise HTTPException(429, str(exc)) from exc
        return {"ok": True, "action": "metaphor"}

    if action == "cancel":
        try:
            await service.submit_decision(uid, str(user.id), {"action": "cancel"})
        except DecisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except CapacityError as exc:
            raise HTTPException(429, str(exc)) from exc
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
        except DecisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except CapacityError as exc:
            raise HTTPException(429, str(exc)) from exc
        return {"ok": True, "action": "generate"}

    # upload
    data = await read_image_upload(
        file, max_bytes=settings.max_upload_bytes, required=True
    )
    manager = request.app.state.manager
    dest_dir = manager.task_tmp(str(user.id), uid)
    dest = Path(dest_dir) / f"hero{_safe_suffix(file.filename)}"
    dest.write_bytes(data)
    decision = {"action": "upload", "local_path": str(dest)}
    try:
        await service.submit_decision(uid, str(user.id), decision)
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"ok": True, "action": "upload"}
