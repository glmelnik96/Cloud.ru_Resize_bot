"""Библиотека знаний по HTTP — чтение каталога, правка и история.

Читать может любой авторизованный пользователь: карточки продуктов это не
секрет, а общий словарь команды. Править и смотреть историю — только под
ролью (admin или kb_editor).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import KbProductIn, KbProductOut, KbProductPatch, KbVersionOut
from app.auth.deps import get_current_user
from app.auth.roles import require_kb_edit
from app.db import models
from app.kb.store import (
    KbConflict,
    KbNotFound,
    create_product,
    history,
    latest_rows,
    refresh_catalog,
    update_product,
)

router = APIRouter(prefix="/api/kb", tags=["kb"])


def kb_out(r: models.KbProduct) -> KbProductOut:
    return KbProductOut(
        slug=r.slug,
        name=r.name,
        version=r.version,
        aliases=list(r.aliases or []),
        tagline=r.tagline or "",
        archived=bool(r.archived),
        updated_by=r.updated_by or "",
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
        block1=r.block1 or "",
        block2=r.block2 or "",
        block3=r.block3 or "",
    )


def version_out(r: models.KbProduct) -> KbVersionOut:
    return KbVersionOut(
        version=r.version,
        name=r.name,
        tagline=r.tagline or "",
        archived=bool(r.archived),
        updated_by=r.updated_by or "",
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
        block1=r.block1 or "",
        block2=r.block2 or "",
        block3=r.block3 or "",
    )


async def _latest(Session, slug: str) -> models.KbProduct:
    rows = await history(Session, slug)
    if not rows:
        raise HTTPException(status_code=404, detail="product not found")
    return rows[0]


@router.get("/products", response_model=list[KbProductOut])
async def list_products(request: Request, include_archived: bool = False):
    await get_current_user(request)
    rows = await latest_rows(
        request.app.state.sessionmaker, include_archived=include_archived
    )
    return [kb_out(r) for r in rows]


@router.post("/products", response_model=KbProductOut, status_code=201)
async def create(request: Request, body: KbProductIn):
    editor = await require_kb_edit(request)
    Session = request.app.state.sessionmaker
    try:
        await create_product(
            Session,
            slug=body.slug,
            fields=body.model_dump(exclude={"slug"}),
            updated_by=editor.email,
        )
    except KbConflict as exc:
        # Тихо перезаписать чужую карточку — худший из возможных исходов:
        # правка ушла бы в граф, а автор оригинала об этом не узнал.
        raise HTTPException(status_code=409, detail="slug already exists") from exc
    # Правка должна быть видна СЛЕДУЮЩЕМУ запуску без рестарта — снапшот БД
    # инжектим в graph.knowledge сразу после записи.
    await refresh_catalog(Session)
    return kb_out(await _latest(Session, body.slug))


@router.put("/products/{slug}", response_model=KbProductOut)
async def update(request: Request, slug: str, body: KbProductPatch):
    editor = await require_kb_edit(request)
    Session = request.app.state.sessionmaker
    try:
        await update_product(
            Session,
            slug=slug,
            fields=body.model_dump(exclude_none=True),
            updated_by=editor.email,
        )
    except KbNotFound as exc:
        raise HTTPException(status_code=404, detail="product not found") from exc
    await refresh_catalog(Session)
    return kb_out(await _latest(Session, slug))


@router.get("/products/{slug}/history", response_model=list[KbVersionOut])
async def product_history(request: Request, slug: str):
    # История — редакторский инструмент (спека отдаёт её админу): читателю
    # важен текущий текст, а не кто и когда его правил.
    await require_kb_edit(request)
    rows = await history(request.app.state.sessionmaker, slug)
    if not rows:
        raise HTTPException(status_code=404, detail="product not found")
    return [version_out(r) for r in rows]
