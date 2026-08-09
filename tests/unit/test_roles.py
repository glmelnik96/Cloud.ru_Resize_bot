"""Роли App3: user по умолчанию, bootstrap-админ, kb_editor, /api/me и /api/admin/roles."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.auth.roles import Access, resolve_access  # noqa: E402
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


async def test_bootstrap_admin_tolerates_whitespace_in_env(Session):
    """Лишний пробел в APP3_BOOTSTRAP_ADMIN — классика .env-файла: из-за него
    система осталась бы вообще без админа и без способа его назначить."""
    u = await _user(Session, "boss@cloud.ru")
    async with Session() as s:
        acc = await resolve_access(s, u, bootstrap_admin=" boss@cloud.ru ")
    assert acc.is_admin is True


async def test_kb_editor_flag_allows_edit_but_not_admin(Session):
    u = await _user(Session, "editor@cloud.ru")
    async with Session() as s:
        s.add(models.UserRole(user_id=u.id, role="user", kb_editor=True))
        await s.commit()
    async with Session() as s:
        acc = await resolve_access(s, u)
    assert acc.can_edit_kb is True
    assert acc.is_admin is False
    # Неизвестная роль (опечатка при ручной правке БД, будущая роль) не должна
    # давать админские права: сравнение строгое, а не «всё, что не user».
    assert Access(role="viewer", kb_editor=False).is_admin is False


def _db(tmp_path):
    """Файл БД приложения — тестам он нужен и для прямого чтения аудита."""
    return tmp_path / "roles.db"


def _app(tmp_path, monkeypatch, **extra):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    cfg = {"db_url": f"sqlite+aiosqlite:///{_db(tmp_path)}"}
    cfg.update(extra)
    return create_app(cfg)


def _role_rows(db_path):
    """Прочитать user_roles тем же приёмом, что и остальные тесты App3:
    отдельный движок к тому же файлу, потому что цикл приложения крутится
    в чужом потоке."""
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def _sel():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            async with async_sessionmaker(eng)() as s:
                rows = (await s.execute(select(models.UserRole))).scalars().all()
                return [
                    {"user_id": r.user_id, "role": r.role,
                     "kb_editor": bool(r.kb_editor), "updated_by": r.updated_by,
                     "updated_at": r.updated_at}
                    for r in rows
                ]
        finally:
            await eng.dispose()

    return asyncio.run(_sel())


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
        # PUT — это и есть эскалация привилегий: гейт здесь важнее, чем на GET.
        assert c.put(
            "/api/admin/roles",
            json={"email": "u@cloud.ru", "role": "admin", "kb_editor": True},
            headers=_HDR,
        ).status_code == 403

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
        # Выданный флаг должен быть видно и в списке, иначе админ не может
        # проверить, кому он раздал права.
        rows = {row["email"]: row for row in c.get("/api/admin/roles", headers=_BOSS).json()}
        assert rows["u@cloud.ru"]["kb_editor"] is True

        # Почту админ копирует откуда придётся: регистр не должен давать 404
        # там, где /api/me показывает того же человека.
        r = c.put(
            "/api/admin/roles",
            json={"email": "U@Cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        assert r.status_code == 200 and r.json()["email"] == "u@cloud.ru"


def test_technical_email_predicate():
    """Что считается учёткой прогона, а не человека. Ошибка в обе стороны стоит
    дорого: спрятать коллегу — значит не выдать ему доступ, показать полсотни
    load-00@qa.local — значит утопить в них коллегу."""
    from app.api.routes_admin import is_technical_email

    for junk in ("", "   ", "qa-iso-1@test.local", "load-00@qa.local",
                 "e2e@cloud.ru", "e2e-smoke@cloud.ru", "smoke@x", "no-at-sign",
                 # Ручные пробы из списка: правилом их не отличить от людей.
                 "t@e.ru", "T@E.ru", "gleb@cloud.ru"):
        assert is_technical_email(junk) is True, junk
    for human in ("g.melnikov96@yandex.ru", "Kedra.108@yandex.ru",
                  "boss@cloud.ru", "e2gor@cloud.ru", "qa.petrov@cloud.ru"):
        assert is_technical_email(human) is False, human


def test_roles_list_hides_technical_users_but_never_hides_rights(tmp_path, monkeypatch):
    """Список доступов — рабочий инструмент поиска коллеги, и прогоны его
    забивают. Прячем их по умолчанию, но с двумя обязательствами: полный состав
    доступен переключателем, а техническая учётка с выданными правами видна
    всегда — невидимый носитель прав это права, которые никто не отзовёт."""
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_BOSS)
        c.get("/api/me", headers=_HDR)
        c.get("/api/me", headers={"X-User-Id": "7", "X-User-Email": "load-00@qa.local"})
        c.get("/api/me", headers={"X-User-Id": "8", "X-User-Email": "e2e@cloud.ru"})

        shown = {r["email"] for r in c.get("/api/admin/roles", headers=_BOSS).json()}
        assert shown == {"boss@cloud.ru", "u@cloud.ru"}

        full = {r["email"] for r in
                c.get("/api/admin/roles?technical=true", headers=_BOSS).json()}
        assert full == shown | {"load-00@qa.local", "e2e@cloud.ru"}

        c.put(
            "/api/admin/roles",
            json={"email": "e2e@cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        rows = {r["email"]: r for r in c.get("/api/admin/roles", headers=_BOSS).json()}
        assert rows["e2e@cloud.ru"]["kb_editor"] is True
        # Отозвали — учётка снова уходит в технические и список опять чистый.
        c.put(
            "/api/admin/roles",
            json={"email": "e2e@cloud.ru", "role": "user", "kb_editor": False},
            headers=_BOSS,
        )
        assert "e2e@cloud.ru" not in {
            r["email"] for r in c.get("/api/admin/roles", headers=_BOSS).json()
        }


def test_admin_cannot_demote_himself(tmp_path, monkeypatch):
    """Один промах в выпадающем списке не должен бриккать администрирование:
    после самопонижения bootstrap уже не поднимет — строка-то есть."""
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_BOSS)
        r = c.put(
            "/api/admin/roles",
            json={"email": "boss@cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        assert r.status_code == 400
        assert c.get("/api/me", headers=_BOSS).json()["role"] == "admin"


def test_put_writes_audit_fields(tmp_path, monkeypatch):
    """«Кто и когда выдал доступ» — единственный след решения админа: строка
    одна на пользователя, прежнее значение затирается без истории."""
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        c.get("/api/me", headers=_BOSS)

        r = c.put(
            "/api/admin/roles",
            json={"email": "u@cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        assert r.status_code == 200
        rows = {row["user_id"]: row for row in _role_rows(_db(tmp_path))}
        assert rows[me["id"]]["updated_by"] == "boss@cloud.ru"
        first_at = rows[me["id"]]["updated_at"]

        # Второй PUT меняет значение (иначе UPDATE мог бы и не уйти в БД):
        # отметка времени обязана сдвинуться, иначе аудит показывает старое.
        r = c.put(
            "/api/admin/roles",
            json={"email": "u@cloud.ru", "role": "user", "kb_editor": False},
            headers=_BOSS,
        )
        assert r.status_code == 200
        rows = {row["user_id"]: row for row in _role_rows(_db(tmp_path))}
        assert rows[me["id"]]["kb_editor"] is False
        assert rows[me["id"]]["updated_at"] > first_at


def test_admin_roles_rejects_unknown_email_and_role(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_BOSS)
        r = c.put(
            "/api/admin/roles",
            json={"email": "ghost@cloud.ru", "role": "admin", "kb_editor": False},
            headers=_BOSS,
        )
        assert r.status_code == 404
        r = c.put(
            "/api/admin/roles",
            json={"email": "boss@cloud.ru", "role": "root", "kb_editor": False},
            headers=_BOSS,
        )
        assert r.status_code == 422
        # Неполный PUT (без kb_editor) не должен молча понижать роль.
        r = c.put(
            "/api/admin/roles",
            json={"email": "boss@cloud.ru", "role": "admin"},
            headers=_BOSS,
        )
        assert r.status_code == 422
