"""kb_products: сид из файлового каталога + чтение последних версий как ProductDoc."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models
from app.db.database import init_db, make_engine, make_sessionmaker
from app.kb.store import load_product_docs, refresh_catalog, seed_from_files
from graph.knowledge import _load_file_catalog


@pytest.fixture
async def Session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_seed_imports_file_catalog_once(Session):
    n = await seed_from_files(Session)
    file_docs = _load_file_catalog()
    assert n == len(file_docs) > 0
    # повторный сид — no-op
    assert await seed_from_files(Session) == 0
    async with Session() as s:
        rows = (await s.execute(select(models.KbProduct))).scalars().all()
    assert {r.slug for r in rows} == {d.slug for d in file_docs}
    assert all(r.version == 1 and r.updated_by == "seed" for r in rows)


async def test_load_docs_equivalent_to_file_catalog(Session):
    """Снапшот-эквивалентность: БД-каталог после сида == файловый (кроме version)."""
    await seed_from_files(Session)
    db_docs = {d.slug: d for d in await load_product_docs(Session)}
    for fd in _load_file_catalog():
        dd = db_docs[fd.slug]
        assert dd.name == fd.name
        assert dd.aliases == fd.aliases
        assert dd.tagline == fd.tagline
        for n in (1, 2, 3):
            assert dd.block(n) == fd.block(n)
        assert dd.version == 1


async def test_refresh_catalog_injects_db_snapshot(Session):
    from graph import knowledge

    await seed_from_files(Session)
    try:
        n = await refresh_catalog(Session)
        assert n > 0
        assert knowledge.load_catalog() == await load_product_docs(Session)
    finally:
        knowledge.set_catalog(None)


async def test_load_docs_takes_latest_version_and_skips_archived(Session):
    await seed_from_files(Session)
    async with Session() as s:
        first = (
            await s.execute(select(models.KbProduct).order_by(models.KbProduct.slug))
        ).scalars().first()
        s.add(
            models.KbProduct(
                slug=first.slug, version=2, name="Edited Name",
                aliases=list(first.aliases), tagline=first.tagline,
                block1="## Блок 1. Новый", block2="", block3="",
                updated_by="admin@test",
            )
        )
        other = (
            await s.execute(select(models.KbProduct).order_by(models.KbProduct.slug.desc()))
        ).scalars().first()
        s.add(
            models.KbProduct(
                slug=other.slug, version=2, name=other.name,
                aliases=list(other.aliases), tagline=other.tagline,
                block1=other.block1, block2=other.block2, block3=other.block3,
                updated_by="admin@test", archived=True,
            )
        )
        await s.commit()
    docs = {d.slug: d for d in await load_product_docs(Session)}
    assert docs[first.slug].name == "Edited Name"
    assert docs[first.slug].version == 2
    assert other.slug not in docs


async def test_latest_rows_hides_archived_by_default(Session):
    from app.kb.store import latest_rows, update_product

    await seed_from_files(Session)
    rows = await latest_rows(Session)
    victim = rows[0].slug
    await update_product(
        Session, slug=victim, fields={"archived": True}, updated_by="admin@test"
    )
    assert victim not in {r.slug for r in await latest_rows(Session)}
    archived = {r.slug: r for r in await latest_rows(Session, include_archived=True)}
    assert archived[victim].archived is True
    assert archived[victim].version == 2


async def test_update_product_appends_version_and_keeps_untouched_fields(Session):
    from app.kb.store import latest_rows, update_product

    await seed_from_files(Session)
    row = (await latest_rows(Session))[0]
    new_version = await update_product(
        Session,
        slug=row.slug,
        fields={"tagline": "новый tagline"},
        updated_by="admin@test",
    )
    assert new_version == row.version + 1
    fresh = {r.slug: r for r in await latest_rows(Session)}[row.slug]
    assert fresh.tagline == "новый tagline"
    assert fresh.name == row.name          # не тронутое поле переносится
    assert fresh.block1 == row.block1
    assert fresh.updated_by == "admin@test"


async def test_update_product_unknown_slug_raises(Session):
    from app.kb.store import KbNotFound, update_product

    await seed_from_files(Session)
    with pytest.raises(KbNotFound):
        await update_product(
            Session, slug="no-such-product", fields={"tagline": "x"}, updated_by="a@b"
        )


async def test_create_product_starts_at_version_1_and_rejects_duplicate(Session):
    from app.kb.store import KbConflict, create_product, latest_rows

    await seed_from_files(Session)
    v = await create_product(
        Session,
        slug="test-product",
        fields={"name": "Test Product", "tagline": "тест", "block1": "## Блок 1. Что это"},
        updated_by="admin@test",
    )
    assert v == 1
    fresh = {r.slug: r for r in await latest_rows(Session)}["test-product"]
    assert fresh.name == "Test Product"
    assert fresh.aliases == ["Test Product"]  # алиас по умолчанию — имя
    with pytest.raises(KbConflict):
        await create_product(
            Session, slug="test-product", fields={"name": "Dup"}, updated_by="a@b"
        )


async def test_history_is_newest_first(Session):
    from app.kb.store import history, latest_rows, update_product

    await seed_from_files(Session)
    slug = (await latest_rows(Session))[0].slug
    await update_product(Session, slug=slug, fields={"tagline": "v2"}, updated_by="a@b")
    await update_product(Session, slug=slug, fields={"tagline": "v3"}, updated_by="a@b")
    versions = [r.version for r in await history(Session, slug)]
    assert versions == [3, 2, 1]
