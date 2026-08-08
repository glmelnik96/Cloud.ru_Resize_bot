"""Библиотека знаний по HTTP: чтение каталога и правка под ролями."""
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


# ── Task 11: запись под ролями ─────────────────────────────────────────

_BOSS = {"X-User-Id": "1", "X-User-Email": "boss@cloud.ru"}


def _admin_app(tmp_path, monkeypatch):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'kbw.db'}",
        "bootstrap_admin": "boss@cloud.ru",
    })


def _first_slug(c) -> str:
    return c.get("/api/kb/products", headers=_BOSS).json()[0]["slug"]


def test_write_requires_role(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        slug = _first_slug(c)
        r = c.put(f"/api/kb/products/{slug}", json={"tagline": "нельзя"}, headers=_HDR)
        assert r.status_code == 403
        r = c.post(
            "/api/kb/products",
            json={"slug": "new-one", "name": "New One"},
            headers=_HDR,
        )
        assert r.status_code == 403
        # История — тоже редакторский инструмент
        assert c.get(f"/api/kb/products/{slug}/history", headers=_HDR).status_code == 403
        # но сам каталог читают все
        assert c.get("/api/kb/products", headers=_HDR).status_code == 200


def test_update_appends_version_and_history(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        slug = _first_slug(c)
        r = c.put(
            f"/api/kb/products/{slug}",
            json={"tagline": "Новый тэглайн", "block1": "## Блок 1. Что это\nНовое."},
            headers=_BOSS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 2 and body["tagline"] == "Новый тэглайн"
        assert body["updated_by"] == "boss@cloud.ru"

        h = c.get(f"/api/kb/products/{slug}/history", headers=_BOSS).json()
        assert [v["version"] for v in h] == [2, 1]
        # История без тел блоков бесполезна: «посмотреть старую версию» — это
        # именно текст, а не номер. Свежая версия — новый, прежняя — прежний.
        new_v, old_v = h[0], h[1]
        assert new_v["tagline"] == "Новый тэглайн"
        assert new_v["block1"] == "## Блок 1. Что это\nНовое."
        assert old_v["tagline"] != "Новый тэглайн"
        assert old_v["block1"] != new_v["block1"]


def test_history_unknown_slug_404(tmp_path, monkeypatch):
    """Без гарда история несуществующей карточки отдала бы 200 [] — «продукт
    есть, просто правок не было», что читается как подтверждение опечатки."""
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        assert c.get("/api/kb/products/ghost/history", headers=_BOSS).status_code == 404


def test_create_and_archive(tmp_path, monkeypatch):
    from graph import knowledge

    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        r = c.post(
            "/api/kb/products",
            json={
                "slug": "evolution-demo", "name": "Evolution Demo",
                # Пустые алиасы приходят из формы сплошь и рядом; в БД такой
                # алиас превратил бы карточку в «матч на любой бриф».
                "aliases": ["", "  ", "демо"], "tagline": "Демо-продукт",
                "block1": "## Блок 1. Что это\nДемо.",
            },
            headers=_BOSS,
        )
        assert r.status_code == 201 and r.json()["version"] == 1
        assert r.json()["aliases"] == ["демо"]
        # Автора правки ставит сервер по сессии, а не клиент.
        assert r.json()["updated_by"] == "boss@cloud.ru"

        # Создание, как и правка, должно доезжать до графа без рестарта.
        fresh = knowledge.get_by_slug("evolution-demo")
        assert fresh is not None and fresh.name == "Evolution Demo"
        assert list(fresh.aliases) == ["демо"]

        # повторный slug — конфликт, а не тихая перезапись
        assert c.post(
            "/api/kb/products",
            json={"slug": "evolution-demo", "name": "Dup"},
            headers=_BOSS,
        ).status_code == 409

        assert c.put(
            "/api/kb/products/evolution-demo", json={"archived": True}, headers=_BOSS
        ).status_code == 200
        slugs = {p["slug"] for p in c.get("/api/kb/products", headers=_BOSS).json()}
        assert "evolution-demo" not in slugs
        slugs = {
            p["slug"]
            for p in c.get(
                "/api/kb/products?include_archived=true", headers=_BOSS
            ).json()
        }
        assert "evolution-demo" in slugs


def test_kb_editor_flag_grants_write_but_not_admin(tmp_path, monkeypatch):
    """Флаг kb_editor — вся суть роли: править библиотеку можно, раздавать
    доступы нельзя. Проверяем по HTTP, иначе подмена гарда на require_admin
    в роутах прошла бы незамеченной."""
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        slug = _first_slug(c)
        # Пользователь заводится в БД первым входом через шлюз — до этого
        # выдавать роль некому (PUT /api/admin/roles ответил бы 404).
        assert c.get("/api/kb/products", headers=_HDR).status_code == 200

        r = c.put(
            "/api/admin/roles",
            json={"email": "u@cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        assert r.status_code == 200 and r.json()["kb_editor"] is True

        w = c.put(
            f"/api/kb/products/{slug}",
            json={"tagline": "правка редактора библиотеки"},
            headers=_HDR,
        )
        assert w.status_code == 200 and w.json()["updated_by"] == "u@cloud.ru"
        assert c.get(f"/api/kb/products/{slug}/history", headers=_HDR).status_code == 200
        assert c.get("/api/admin/roles", headers=_HDR).status_code == 403


def test_create_validates_slug_and_name(tmp_path, monkeypatch):
    """Схема записи — первый рубеж: кривой slug ломает и ссылки, и поиск по
    брифу, а имя из одной буквы бессмысленно в списке продуктов."""
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        assert c.post(
            "/api/kb/products",
            json={"slug": "Bad Slug", "name": "Нормальное имя"},
            headers=_BOSS,
        ).status_code == 422
        assert c.post(
            "/api/kb/products", json={"slug": "ok-slug", "name": "X"}, headers=_BOSS
        ).status_code == 422


def test_update_unknown_slug_404(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        assert c.put(
            "/api/kb/products/ghost", json={"tagline": "x"}, headers=_BOSS
        ).status_code == 404


def test_edit_reaches_graph_catalog_without_restart(tmp_path, monkeypatch):
    """PUT → version+1 → refresh_catalog → graph.knowledge отдаёт новый текст."""
    from graph import knowledge

    try:
        with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
            slug = _first_slug(c)
            before = knowledge.get_by_slug(slug)
            assert before is not None and before.version == 1

            r = c.put(
                f"/api/kb/products/{slug}",
                json={"tagline": "Свежий тэглайн из UI"},
                headers=_BOSS,
            )
            assert r.status_code == 200 and r.json()["version"] == 2

            after = knowledge.get_by_slug(slug)
            assert after is not None
            assert after.version == 2
            assert after.tagline == "Свежий тэглайн из UI"
    finally:
        # lifespan инжектил снапшот в глобальный модуль графа — снимаем,
        # чтобы соседние тесты видели файловый каталог.
        knowledge.set_catalog(None)
