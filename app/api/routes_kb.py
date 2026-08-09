"""Библиотека знаний по HTTP — чтение каталога, правка и история.

Читать может любой авторизованный пользователь: карточки продуктов это не
секрет, а общий словарь команды. Править и смотреть историю — только под
ролью (admin или kb_editor).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.schemas import (
    ExperienceOut,
    KbProductIn,
    KbProductOut,
    KbProductPatch,
    KbVersionOut,
)
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
    except IntegrityError as exc:
        # Гонка двух редакторов: exists-проверка (или чтение prev.version)
        # прошла у обоих, а уникальный индекс (slug, version) поймал второго.
        # Для человека это тот же конфликт, а не поломка сервиса.
        raise HTTPException(
            status_code=409, detail="карточку в этот момент правил кто-то ещё"
        ) from exc
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
    except IntegrityError as exc:
        # Гонка двух редакторов: exists-проверка (или чтение prev.version)
        # прошла у обоих, а уникальный индекс (slug, version) поймал второго.
        # Для человека это тот же конфликт, а не поломка сервиса.
        raise HTTPException(
            status_code=409, detail="карточку в этот момент правил кто-то ещё"
        ) from exc
    await refresh_catalog(Session)
    return kb_out(await _latest(Session, slug))


@router.get("/experience", response_model=list[ExperienceOut])
async def list_experience(request: Request, limit: int = 50):
    """Лента отмеченных исходов для человека — ВСЕ продукты и ОБА исхода.

    Это не то, что видит копирайтер: в промпт уходит только outcome="shipped"
    и только по продукту текущего брифа (graph.knowledge.experience_for).
    Забракованное живёт здесь и нигде больше — команде нужно видеть, куда уже
    ходили и что отвергли, а модели «вот так не надо» работает как подсказка
    повторить неудачу.

    Читать может любой авторизованный, хотя сами задачи закрыты по владельцу
    (_load_owned в routes_tasks): опыт — общий актив команды, а не личная
    история запусков. Тела задач, файлы и брифы отсюда не видны — только
    решения, которые команда уже приняла вслух.
    """
    await get_current_user(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        rows = (
            await s.execute(
                select(models.KbRun)
                # По updated_at, а не created_at: только что переставленная
                # отметка обязана быть сверху, иначе лента спорит сама с собой.
                .order_by(models.KbRun.updated_at.desc(), models.KbRun.id.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
    return [
        ExperienceOut(
            slug=r.slug or "",
            outcome=r.outcome,
            slogan=r.slogan or "",
            anchor=r.anchor or "",
            persona_segment=r.persona_segment or "",
            comment=r.comment or "",
            created_at=r.created_at.isoformat() if r.created_at else None,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
        for r in rows
    ]


@router.get("/products/{slug}/history", response_model=list[KbVersionOut])
async def product_history(request: Request, slug: str):
    # История — редакторский инструмент (спека отдаёт её админу): читателю
    # важен текущий текст, а не кто и когда его правил.
    await require_kb_edit(request)
    rows = await history(request.app.state.sessionmaker, slug)
    if not rows:
        raise HTTPException(status_code=404, detail="product not found")
    return [version_out(r) for r in rows]
