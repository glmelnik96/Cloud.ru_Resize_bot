"""Роли и права: кто читает библиотеку знаний, а кто её правит.

Роль лежит в `user_roles` (PK = users.id); отсутствие строки = обычный
пользователь. Первый админ поднимается из APP3_BOOTSTRAP_ADMIN при первом же
обращении этого email — на пустой БД иначе некому раздать роли.

Гейты (`require_admin`, `require_kb_edit`) — обычные async-функции, а не
FastAPI-Depends: остальные роуты App3 так же вызывают `get_current_user(request)`
руками, и смешивать два стиля в одном приложении хуже, чем повторить `await`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db import models

ADMIN = "admin"
USER = "user"
ROLES = (ADMIN, USER)


@dataclass(frozen=True)
class Access:
    role: str
    kb_editor: bool

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def can_edit_kb(self) -> bool:
        return self.is_admin or self.kb_editor


async def resolve_access(
    session: AsyncSession, user: models.User, *, bootstrap_admin: str = ""
) -> Access:
    """Вернуть права пользователя, при необходимости подняв bootstrap-админа.

    Строку bootstrap-админа функция только вставляет — коммитит вызывающий
    (`current_access` это делает). Иначе вставка тихо потеряется вместе с
    сессией, а следующий вход снова пойдёт по bootstrap-ветке.
    """
    row = await session.get(models.UserRole, user.id)
    if row is None:
        if bootstrap_admin and _same_email(user.email, bootstrap_admin):
            row = models.UserRole(user_id=user.id, role=ADMIN, updated_by="bootstrap")
            try:
                async with session.begin_nested():
                    session.add(row)
            except IntegrityError:
                # Параллельный первый вход того же админа успел вставить строку.
                # Это не ошибка, а тот же результат другим путём — перечитываем.
                row = await session.get(models.UserRole, user.id)
                if row is None:
                    return Access(role=USER, kb_editor=False)
        else:
            return Access(role=USER, kb_editor=False)
    return Access(role=row.role, kb_editor=bool(row.kb_editor))


def _same_email(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


async def current_access(request: Request) -> tuple[models.User, Access]:
    user = await get_current_user(request)
    cfg = getattr(request.app.state, "settings", {}) or {}
    Session = request.app.state.sessionmaker
    async with Session() as s:
        access = await resolve_access(
            s, user, bootstrap_admin=cfg.get("bootstrap_admin", "")
        )
        await s.commit()
    return user, access


async def require_admin(request: Request) -> models.User:
    user, access = await current_access(request)
    if not access.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


async def require_kb_edit(request: Request) -> models.User:
    user, access = await current_access(request)
    if not access.can_edit_kb:
        raise HTTPException(status_code=403, detail="kb edit not allowed")
    return user
