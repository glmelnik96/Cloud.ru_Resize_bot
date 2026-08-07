"""SQLAlchemy ORM models (App1 schema).

Task.status extends App1's lifecycle with two parked HITL states and a
cancelled terminal state, because /new is interactive (pauses for text
approval and hero image) rather than fire-and-forget.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_user_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    yandex_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="user")


class UsageEvent(Base):
    """Append-only usage log (cross-app contract 2026-06-22). One row per
    completed creatives operation; identity comes from the gateway headers.
    Retention NEVER touches this table — it is anonymised (no files, no full
    prompt; meta holds only safe whitelisted params)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    app: Mapped[str] = mapped_column(String(16), index=True)  # creatives
    gateway_user_id: Mapped[Optional[str]] = mapped_column(
        String(64), index=True, nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    event: Mapped[str] = mapped_column(String(32))  # variant
    workflow: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # done|failed|cancelled
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    workflow: Mapped[str] = mapped_column(String(32))
    prompt: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # queued | running | awaiting_text | awaiting_image | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued")
    result_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="tasks")


class KbProduct(Base):
    """Библиотека знаний, слой «факты» — append-only версии карточек продуктов.

    Правка = новая строка с version+1 (историю видно, откатывать легко);
    чтение берёт максимальную версию неархивного slug. Сид — из
    config/knowledge/*.md (version=1, updated_by="seed")."""

    __tablename__ = "kb_products"
    __table_args__ = (UniqueConstraint("slug", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    tagline: Mapped[str] = mapped_column(Text, default="")
    block1: Mapped[str] = mapped_column(Text, default="")
    block2: Mapped[str] = mapped_column(Text, default="")
    block3: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    archived: Mapped[bool] = mapped_column(default=False)
