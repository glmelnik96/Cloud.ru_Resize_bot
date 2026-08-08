"""Админский роутер: раздача ролей. Доступ — только role=admin."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.api.schemas import RoleIn, RoleOut
from app.auth.roles import ADMIN, require_admin
from app.db import models

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(request: Request):
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
