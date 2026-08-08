"""Админский роутер: раздача ролей. Доступ — только role=admin."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.schemas import RoleIn, RoleOut
from app.auth.roles import require_admin
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
                select(models.User).where(models.User.email == body.email)
            )
        ).scalars().first()
        if target is None:
            # Пользователь заводится в БД при первом входе через шлюз — роль
            # заранее выдать некому, и молча создавать пустышку хуже, чем 404.
            raise HTTPException(status_code=404, detail="user not found")
        row = await s.get(models.UserRole, target.id)
        if row is None:
            row = models.UserRole(user_id=target.id)
            s.add(row)
        row.role = body.role
        row.kb_editor = body.kb_editor
        row.updated_by = admin.email
        await s.commit()
    return RoleOut(email=target.email, role=body.role, kb_editor=body.kb_editor)
