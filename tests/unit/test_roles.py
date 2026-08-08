"""Роли App3: user по умолчанию, bootstrap-админ, kb_editor, /api/me и /api/admin/roles."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.auth.roles import resolve_access  # noqa: E402
from app.db import models  # noqa: E402
from app.db.database import init_db, make_engine, make_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}
_BOSS = {"X-User-Id": "1", "X-User-Email": "boss@cloud.ru"}


@pytest.fixture
async def Session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def _user(Session, email: str) -> models.User:
    async with Session() as s:
        u = models.User(gateway_user_id=email, yandex_id=f"y:{email}", email=email)
        s.add(u)
        await s.commit()
        return u


async def test_no_row_means_plain_user(Session):
    u = await _user(Session, "u@cloud.ru")
    async with Session() as s:
        acc = await resolve_access(s, u, bootstrap_admin="boss@cloud.ru")
    assert acc.role == "user"
    assert acc.is_admin is False
    assert acc.can_edit_kb is False


async def test_bootstrap_admin_gets_row_once(Session):
    u = await _user(Session, "Boss@Cloud.ru")  # регистр не важен
    async with Session() as s:
        acc = await resolve_access(s, u, bootstrap_admin="boss@cloud.ru")
        await s.commit()
    assert acc.is_admin is True and acc.can_edit_kb is True
    async with Session() as s:
        row = await s.get(models.UserRole, u.id)
    assert row is not None and row.role == "admin" and row.updated_by == "bootstrap"


async def test_kb_editor_flag_allows_edit_but_not_admin(Session):
    u = await _user(Session, "editor@cloud.ru")
    async with Session() as s:
        s.add(models.UserRole(user_id=u.id, role="user", kb_editor=True))
        await s.commit()
    async with Session() as s:
        acc = await resolve_access(s, u)
    assert acc.can_edit_kb is True
    assert acc.is_admin is False


def _app(tmp_path, monkeypatch, **extra):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    cfg = {"db_url": f"sqlite+aiosqlite:///{tmp_path / 'roles.db'}"}
    cfg.update(extra)
    return create_app(cfg)


def test_me_exposes_role(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        assert me["role"] == "user" and me["can_edit_kb"] is False
        boss = c.get("/api/me", headers=_BOSS).json()
        assert boss["role"] == "admin" and boss["can_edit_kb"] is True


def test_admin_roles_list_and_grant(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_HDR)      # оба пользователя появились в БД
        c.get("/api/me", headers=_BOSS)     # и админ поднялся из bootstrap

        assert c.get("/api/admin/roles", headers=_HDR).status_code == 403

        r = c.get("/api/admin/roles", headers=_BOSS)
        assert r.status_code == 200
        emails = {row["email"]: row for row in r.json()}
        assert emails["u@cloud.ru"]["role"] == "user"
        assert emails["boss@cloud.ru"]["role"] == "admin"

        r = c.put(
            "/api/admin/roles",
            json={"email": "u@cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        assert r.status_code == 200 and r.json()["kb_editor"] is True
        assert c.get("/api/me", headers=_HDR).json()["can_edit_kb"] is True


def test_admin_roles_rejects_unknown_email_and_role(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_BOSS)
        r = c.put(
            "/api/admin/roles",
            json={"email": "ghost@cloud.ru", "role": "admin"},
            headers=_BOSS,
        )
        assert r.status_code == 404
        r = c.put(
            "/api/admin/roles",
            json={"email": "boss@cloud.ru", "role": "root"},
            headers=_BOSS,
        )
        assert r.status_code == 422
