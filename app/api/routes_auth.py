"""Auth route: current user resolved from the trusted gateway header (App1)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import UserOut
from app.auth.roles import current_access

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/me", response_model=UserOut)
async def me(request: Request):
    # Роль отдаём вместе с профилем: страница библиотеки решает по ней, что
    # показывать — только чтение или кнопки правки.
    user, access = await current_access(request)
    return UserOut(
        id=user.id, email=user.email, display_name=user.display_name,
        role=access.role, can_edit_kb=access.can_edit_kb,
    )
