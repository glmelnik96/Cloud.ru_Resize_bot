"""Auth dependencies: current-user resolver from the trusted gateway header.

Ported verbatim from App1. The gateway authenticates the user (Yandex OAuth +
whitelist), strips any client-supplied X-User-* headers, and injects trusted
X-User-Id / X-User-Email. We trust ONLY those; no cookies, no logins here.
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


async def resolve_user_by_header(
    session: AsyncSession, *, gateway_user_id: str, email: str, display_name: str
) -> models.User:
    res = await session.execute(
        select(models.User).where(models.User.gateway_user_id == gateway_user_id)
    )
    user = res.scalar_one_or_none()
    if user is None:
        user = models.User(
            gateway_user_id=gateway_user_id,
            yandex_id=f"gw:{gateway_user_id}",  # placeholder for yandex_id uniqueness
            email=email,
            display_name=display_name,
        )
        session.add(user)
        await session.flush()
    else:
        user.email = email
    return user


async def get_current_user(request: Request) -> models.User:
    gid = request.headers.get("X-User-Id")
    if not gid:
        raise HTTPException(status_code=401, detail="not authenticated")
    email = request.headers.get("X-User-Email", "")
    Session = request.app.state.sessionmaker
    async with Session() as s:
        user = await resolve_user_by_header(
            s, gateway_user_id=gid, email=email, display_name=email
        )
        await s.commit()
        return user
