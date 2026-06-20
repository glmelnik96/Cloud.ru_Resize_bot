"""Pydantic response/request schemas for the App3 API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str


class CreateTaskIn(BaseModel):
    """The /new brief fields the wizard used to collect in three steps."""

    product: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    audience: str = Field(min_length=1)


class TaskOut(BaseModel):
    task_uid: str
    workflow: str
    status: str
    result_url: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None


class TextDecisionIn(BaseModel):
    """Resume the text-approve interrupt (HITL pause #1)."""

    action: Literal["approve", "regenerate", "refine", "cancel"]
    comment: Optional[str] = None

