"""Библиотека знаний по HTTP: чтение каталога (правка — Task 11)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}


def _app(tmp_path, monkeypatch):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"})


def test_products_list_is_seeded_and_shaped(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/api/kb/products", headers=_HDR)
        assert r.status_code == 200
        items = r.json()
        assert items, "сид библиотеки знаний не отработал"
        first = items[0]
        assert set(first) >= {"slug", "name", "version", "tagline", "aliases", "archived"}
        assert first["version"] == 1
        assert first["archived"] is False


def test_products_list_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/api/kb/products").status_code == 401


def test_archived_product_hidden_unless_requested(tmp_path, monkeypatch):
    """Архивный продукт не виден в обычном списке, но виден при include_archived=true."""
    import asyncio

    from app.db.database import make_engine, make_sessionmaker
    from app.kb.store import update_product

    # Запускаем приложение один раз, чтобы сид отработал и создал записи в БД.
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"
    with TestClient(_app(tmp_path, monkeypatch)):
        pass

    # Архивируем первый доступный slug через store — не вставляем строки вручную.
    async def _do_archive():
        engine = make_engine(db_url)
        sm = make_sessionmaker(engine)
        from app.kb.store import latest_rows
        rows = await latest_rows(sm)
        assert rows, "сид не создал ни одного продукта"
        target_slug = rows[0].slug
        await update_product(sm, slug=target_slug, fields={"archived": True}, updated_by="test")
        await engine.dispose()
        return target_slug

    slug = asyncio.run(_do_archive())

    # Открываем свежий TestClient на той же БД — сид не перезапишет архивную запись.
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        # Без флага — продукт не должен появляться.
        items_default = c.get("/api/kb/products", headers=_HDR).json()
        slugs_default = [i["slug"] for i in items_default]
        assert slug not in slugs_default, (
            f"архивный продукт '{slug}' виден без include_archived"
        )

        # С флагом — продукт должен быть.
        items_with = c.get("/api/kb/products?include_archived=true", headers=_HDR).json()
        slugs_with = [i["slug"] for i in items_with]
        assert slug in slugs_with, (
            f"архивный продукт '{slug}' не виден при include_archived=true"
        )
