"""Библиотека знаний по HTTP — чтение каталога.

Читать может любой авторизованный пользователь: карточки продуктов это не
секрет, а общий словарь команды. Правка и история — Task 11 (под ролями).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import KbProductOut
from app.auth.deps import get_current_user
from app.db import models
from app.kb.store import latest_rows

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


@router.get("/products", response_model=list[KbProductOut])
async def list_products(request: Request, include_archived: bool = False):
    await get_current_user(request)
    rows = await latest_rows(
        request.app.state.sessionmaker, include_archived=include_archived
    )
    return [kb_out(r) for r in rows]
