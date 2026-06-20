"""Auth route: current user resolved from the trusted gateway header (App1)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import UserOut
from app.auth.deps import get_current_user

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/me", response_model=UserOut)
async def me(request: Request):
    user = await get_current_user(request)
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)
