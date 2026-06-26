"""Pydantic response/request schemas for the App3 API."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str


class CreateTaskIn(BaseModel):
    """The /new brief fields the wizard collects in three steps.

    `emotion` is the feeling/образ the offer must evoke — formula
    "[чувство] + [образ/ассоциация]" (e.g. «уверенность и контроль — будто
    всё управление под рукой»). It replaces the former marketing `goal` field;
    the marketing goal is now inferred downstream by parse_brief.
    """

    product: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    emotion: str = Field(min_length=1)


class TaskOut(BaseModel):
    task_uid: str
    workflow: str
    status: str
    prompt: Optional[str] = None
    result_url: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    brief: Dict[str, str] = Field(default_factory=dict)


class TextDecisionIn(BaseModel):
    """Resume the text-approve interrupt (HITL pause #1).

    The user reviews the SET of 12 ranked propositions and either accepts the
    whole set, regenerates a fresh 12, or cancels. There is no per-candidate
    refine in the App3 redesign (2026-06-21).
    """

    action: Literal["approve", "regenerate", "cancel"]

