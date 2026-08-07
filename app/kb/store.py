"""Слой «факты» библиотеки знаний: kb_products → graph.knowledge.ProductDoc.

Граф НЕ импортирует app (import-arch стражи): app-слой читает БД и инжектит
снапшот каталога через knowledge.set_catalog(). Правка карточки = новая строка
version+1; здесь только сид и чтение (CRUD — в план 2)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import models
from graph.knowledge import ProductDoc, load_catalog


async def seed_from_files(sessionmaker) -> int:
    """Одноразовый сид: пустая kb_products <- файловый каталог (version=1)."""
    async with sessionmaker() as s:
        n = (await s.execute(select(func.count(models.KbProduct.id)))).scalar_one()
        if n:
            return 0
        docs = load_catalog()
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


async def load_product_docs(sessionmaker) -> tuple[ProductDoc, ...]:
    """Последняя версия каждого неархивного продукта как ProductDoc."""
    async with sessionmaker() as s:
        rows = (await s.execute(select(models.KbProduct))).scalars().all()
    latest: dict[str, models.KbProduct] = {}
    for r in rows:
        if r.slug not in latest or r.version > latest[r.slug].version:
            latest[r.slug] = r
    return tuple(
        ProductDoc(
            slug=r.slug,
            name=r.name,
            aliases=tuple(r.aliases or [r.name]),
            tagline=r.tagline,
            body="\n\n".join(b for b in (r.block1, r.block2, r.block3) if b),
            version=r.version,
        )
        for r in sorted(latest.values(), key=lambda r: r.slug)
        if not r.archived
    )
