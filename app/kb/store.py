"""Слой «факты» библиотеки знаний: kb_products → graph.knowledge.ProductDoc.

Граф НЕ импортирует app (import-arch стражи): app-слой читает БД и инжектит
снапшот каталога через knowledge.set_catalog(). Правка карточки = новая строка
version+1: история видна целиком, откат — это ещё одна правка."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import models
from graph.knowledge import ProductDoc, _load_file_catalog


class KbNotFound(Exception):
    """Правка карточки, которой нет ни в одной версии."""


class KbConflict(Exception):
    """Создание карточки с уже занятым slug."""


# Поля, которые редактирует человек. Всё остальное (slug/version/updated_*)
# ставит сам store — иначе можно было бы переписать историю через API.
_EDITABLE = ("name", "aliases", "tagline", "block1", "block2", "block3", "archived")


async def seed_from_files(sessionmaker) -> int:
    """Одноразовый сид: пустая kb_products <- файловый каталог (version=1)."""
    async with sessionmaker() as s:
        n = (await s.execute(select(func.count(models.KbProduct.id)))).scalar_one()
        if n:
            return 0
        docs = _load_file_catalog()
        for doc in docs:
            s.add(
                models.KbProduct(
                    slug=doc.slug,
                    version=1,
                    name=doc.name,
                    aliases=list(doc.aliases),
                    tagline=doc.tagline,
                    block1=doc.block(1),
                    block2=doc.block(2),
                    block3=doc.block(3),
                    updated_by="seed",
                )
            )
        await s.commit()
        return len(docs)


async def latest_rows(sessionmaker, *, include_archived: bool = False) -> list[models.KbProduct]:
    """Последняя версия каждого slug, по алфавиту. Архивные — по флагу."""
    async with sessionmaker() as s:
        rows = (await s.execute(select(models.KbProduct))).scalars().all()
    latest: dict[str, models.KbProduct] = {}
    for r in rows:
        if r.slug not in latest or r.version > latest[r.slug].version:
            latest[r.slug] = r
    out = sorted(latest.values(), key=lambda r: r.slug)
    return out if include_archived else [r for r in out if not r.archived]


async def history(sessionmaker, slug: str) -> list[models.KbProduct]:
    """Все версии карточки, свежая первой."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(models.KbProduct)
                .where(models.KbProduct.slug == slug)
                .order_by(models.KbProduct.version.desc())
            )
        ).scalars().all()
    return list(rows)


async def create_product(sessionmaker, *, slug: str, fields: dict, updated_by: str) -> int:
    """Новая карточка (version=1). KbConflict, если slug уже занят."""
    async with sessionmaker() as s:
        exists = (
            await s.execute(select(models.KbProduct.id).where(models.KbProduct.slug == slug))
        ).first()
        if exists:
            raise KbConflict(slug)
        name = fields.get("name") or slug
        s.add(
            models.KbProduct(
                slug=slug,
                version=1,
                name=name,
                aliases=list(fields.get("aliases") or [name]),
                tagline=fields.get("tagline") or "",
                block1=fields.get("block1") or "",
                block2=fields.get("block2") or "",
                block3=fields.get("block3") or "",
                updated_by=updated_by,
            )
        )
        await s.commit()
        return 1


async def update_product(sessionmaker, *, slug: str, fields: dict, updated_by: str) -> int:
    """Правка = строка version+1: непереданные поля переносятся из последней
    версии. Возвращает номер новой версии. KbNotFound, если slug неизвестен."""
    async with sessionmaker() as s:
        prev = (
            await s.execute(
                select(models.KbProduct)
                .where(models.KbProduct.slug == slug)
                .order_by(models.KbProduct.version.desc())
                .limit(1)
            )
        ).scalars().first()
        if prev is None:
            raise KbNotFound(slug)
        data = {k: getattr(prev, k) for k in _EDITABLE}
        data["aliases"] = list(prev.aliases or [])
        data.update({k: v for k, v in fields.items() if k in _EDITABLE and v is not None})
        # Тот же дефолт, что и в create_product. Стереть все алиасы из формы —
        # это не «отключить распознавание»: load_product_docs всё равно подставит
        # имя, и в графе продукт продолжит находиться. Сохранённый пустой список
        # врал бы редактору — карточка показывала бы, что синонимов нет вовсе.
        data["aliases"] = list(data["aliases"]) or [data["name"]]
        s.add(
            models.KbProduct(
                slug=slug, version=prev.version + 1, updated_by=updated_by, **data
            )
        )
        await s.commit()
        return prev.version + 1


async def load_product_docs(sessionmaker) -> tuple[ProductDoc, ...]:
    """Последняя версия каждого неархивного продукта как ProductDoc."""
    rows = await latest_rows(sessionmaker)
    return tuple(
        ProductDoc(
            slug=r.slug,
            name=r.name,
            aliases=tuple(r.aliases or [r.name]),
            tagline=r.tagline,
            body="\n\n".join(b for b in (r.block1, r.block2, r.block3) if b),
            version=r.version,
        )
        for r in rows
    )


async def refresh_catalog(sessionmaker) -> int:
    """Перечитать kb_products и инжектнуть снапшот в граф. Возвращает размер."""
    from graph import knowledge

    docs = await load_product_docs(sessionmaker)
    knowledge.set_catalog(docs)
    return len(docs)
