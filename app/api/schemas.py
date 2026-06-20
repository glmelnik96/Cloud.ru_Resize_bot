"""Pydantic response/request schemas for the App3 API."""
from __future__ import annotations

from pydantic import BaseModel


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
