"""Per-user session bookkeeping for in-flight LangGraph runs.

Single-session per user (per HANDOFF §3 HITL & Drafts). Lives in
``Application.bot_data["sessions"]`` — process-local; not durable.
LangGraph state itself is persisted in Redis via the checkpointer, so a
restart can resume by reading thread_id from Redis if needed. For M2 we
treat process restarts as cancellations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    user_id: int
    chat_id: int
    thread_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "wizard"  # wizard | running | awaiting_hitl | awaiting_refine | awaiting_image_hitl | awaiting_image_refine | done | cancelled
    wizard_data: dict[str, Any] = field(default_factory=dict)
    hitl_message_id: int | None = None
    # Single message we keep edit'ing through the run to show "Этап N/M: ..." to user.
    progress_message_id: int | None = None
    prior_variant: dict[str, Any] | None = None


def get_session_store(bot_data: dict[str, Any]) -> dict[int, Session]:
    store = bot_data.setdefault("sessions", {})
    return store  # type: ignore[return-value]


def get_active(bot_data: dict[str, Any], user_id: int) -> Session | None:
    return get_session_store(bot_data).get(user_id)


def put(bot_data: dict[str, Any], session: Session) -> None:
    get_session_store(bot_data)[session.user_id] = session


def drop(bot_data: dict[str, Any], user_id: int) -> None:
    get_session_store(bot_data).pop(user_id, None)
