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
