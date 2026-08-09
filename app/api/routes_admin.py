"""Админский роутер: раздача ролей. Доступ — только role=admin."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.api.schemas import RoleIn, RoleOut
from app.auth.roles import ADMIN, require_admin
from app.db import models

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Локалпарты, которые заводят прогоны, а не люди: нагрузка, e2e, дымовые тесты.
_TECHNICAL_PREFIXES = ("e2e", "smoke", "load-", "qa-", "test-")
# Домены-песочницы: в них не бывает почтового ящика, значит не бывает и человека.
_TECHNICAL_DOMAINS = (".local", ".test", ".invalid", ".example")
# Ручные пробы, оставшиеся в базе до запуска. Под правила они не подходят —
# выглядят как настоящие адреса, — но людей за ними нет. Списком, а не
# правилом: правило, которое накрыло бы `t@e.ru`, накрыло бы и живого коллегу
# с коротким адресом, а это уже не выданный доступ.
_TECHNICAL_EMAILS = frozenset({"t@e.ru", "gleb@cloud.ru"})


def is_technical_email(email: str) -> bool:
    """Учётка заведена прогоном, а не человеком.

    Список доступов нужен для одного действия: найти коллегу и выдать ему право
    править библиотеку. Каждая строка `load-07@qa.local` — это строка, в которой
    коллеги нет; когда таких строк больше, чем людей, список перестают читать и
    начинают в нём искать, а поиск глазами по десяткам однотипных адресов —
    ровно тот способ выдать доступ не тому. Поэтому прячем по умолчанию, но не
    удаляем: за учётками висит история usage_events, и врать о составе БД
    список не должен — переключатель возвращает полную картину.
    """
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        # Пустой email приходит от прогонов, которые шлют только X-User-Id.
        return True
    if addr in _TECHNICAL_EMAILS:
        return True
    local, _, domain = addr.partition("@")
    if domain.endswith(_TECHNICAL_DOMAINS):
        return True
    if "." not in domain:
        # `smoke@x` — доменом это не является, отправить туда письмо нельзя.
        return True
    return local.startswith(_TECHNICAL_PREFIXES)


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(request: Request, technical: bool = False):
    await require_admin(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        users = (
            await s.execute(select(models.User).order_by(models.User.email))
        ).scalars().all()
        rows = {
            r.user_id: r
            for r in (await s.execute(select(models.UserRole))).scalars().all()
        }
    out = []
    for u in users:
        r = rows.get(u.id)
        has_rights = r is not None and (r.role != "user" or r.kb_editor)
        if not technical and not has_rights and is_technical_email(u.email):
            # Технические прячем, но только пока они пустые. Если техническому
            # адресу когда-то выдали права, строку показываем при любом фильтре:
            # невидимый носитель прав — это права, которые никто не отзовёт.
            continue
        out.append(
            RoleOut(
                email=u.email,
                role=r.role if r else "user",
                kb_editor=bool(r.kb_editor) if r else False,
            )
        )
    return out


@router.put("/roles", response_model=RoleOut)
async def set_role(request: Request, body: RoleIn):
    admin = await require_admin(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        target = (
            await s.execute(
                select(models.User)
                .where(func.lower(models.User.email) == body.email.strip().lower())
                # users.email не уникален (ключ — gateway_user_id): порядок по id
                # делает выбор предсказуемым, а не «какой строкой ляжет».
                .order_by(models.User.id)
            )
        ).scalars().first()
        if target is None:
            # Пользователь заводится в БД при первом входе через шлюз — роль
            # заранее выдать некому, и молча создавать пустышку хуже, чем 404.
            raise HTTPException(status_code=404, detail="user not found")
        if target.id == admin.id and body.role != ADMIN:
            # Разжаловать себя = навсегда потерять доступ к этому же эндпоинту:
            # bootstrap уже не поднимет (строка есть), останется правка БД руками.
            raise HTTPException(status_code=400, detail="cannot demote yourself")
        row = await s.get(models.UserRole, target.id)
        if row is None:
            row = models.UserRole(user_id=target.id)
            s.add(row)
        row.role = body.role
        row.kb_editor = body.kb_editor
        row.updated_by = admin.email
        await s.commit()
        # Ответ собираем из строки БД, а не из тела запроса: при серверной
        # нормализации (email нашли регистронезависимо) они уже расходятся.
        return RoleOut(email=target.email, role=row.role, kb_editor=row.kb_editor)
