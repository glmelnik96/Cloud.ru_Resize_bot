"""Pydantic response/request schemas for the App3 API."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str = "user"
    can_edit_kb: bool = False


class CreateTaskIn(BaseModel):
    """The /new brief the wizard collects.

    `emotion` is the feeling/образ the offer must evoke — formula
    "[чувство] + [образ/ассоциация]" (e.g. «уверенность и контроль — будто
    всё управление под рукой»).

    `notes` and `source_url` are optional grounding (2026-07-28): the free-form
    field for anything that fits nowhere else but must not be missed, and a
    link the pipeline reads so the marketer doesn't have to retype a product
    page. Both empty → the run behaves exactly as before.
    """

    product: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    emotion: str = Field(min_length=1)
    notes: str = Field(default="", max_length=4000)
    source_url: str = Field(default="", max_length=2000)
    # Карточка библиотеки знаний: "auto" — найти по тексту брифа (как было),
    # "none" — не подмешивать карточку вовсе, иначе конкретный slug.
    product_slug: str = Field(default="auto", max_length=64)


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
    # Per-banner ranked cards (slogan/body/cta/hook_angle/score/reason +
    # scenario), aligned by index with `images` — the final-grid captions.
    cards: List[Dict[str, Any]] = Field(default_factory=list)
    # «Как сделан этот баннер»: победитель, персона, карточка знаний, метафора.
    recipe: dict[str, Any] = Field(default_factory=dict)


class PersonaIn(BaseModel):
    """Персона, отредактированная человеком на остановке «Кому пишем».

    Поля повторяют graph.state.Persona, но списки якорей обязательны:
    из болей/мотиваций/возражений собираются 24 черновика, и пустая персона
    просто сожгла бы этап. Возражения допускаются пустыми — они есть не всегда.
    """

    segment: str = Field(min_length=1, max_length=200)
    age_range: str = Field(default="", max_length=64)
    pain_points: list[str] = Field(min_length=1)
    motivations: list[str] = Field(min_length=1)
    objections: list[str] = Field(default_factory=list)
    communication_style: str = Field(default="", max_length=600)

    @field_validator("pain_points", "motivations", "objections", mode="after")
    @classmethod
    def _strip_anchors(cls, v: list[str]) -> list[str]:
        """Пустая строка — такой же мусорный якорь, как и отсутствие списка."""
        return [s.strip() for s in v if s.strip()]

    @field_validator("pain_points", "motivations", mode="after")
    @classmethod
    def _anchors_survive_stripping(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("боли и мотивации не могут состоять из пустых строк")
        return v


class PersonaDecisionIn(BaseModel):
    """Resume остановки «Кому пишем».

    approve без persona — согласие с тем, что вывел граф; approve с persona —
    правка без повторного LLM-вызова; regenerate — вывести персону заново.
    """

    action: Literal["approve", "regenerate", "cancel"]
    persona: PersonaIn | None = None


class TextDecisionIn(BaseModel):
    """Resume остановки «Выбор текстов».

    Человек смотрит 12 предложений и либо принимает набор, либо просит
    сгенерировать заново, либо отменяет. `winner_id` (2026-08-07) помечает,
    какой текст ведёт: граф ставит его в ranked[0], и метафора/рендер строятся
    вокруг него. Без winner_id остаётся скоринговый порядок.
    """

    action: Literal["approve", "regenerate", "cancel"]
    winner_id: str | None = Field(default=None, max_length=64)


class KbProductOut(BaseModel):
    """Карточка продукта в её последней версии.

    Блоки отдаются целиком: каталог небольшой (десяток продуктов), а страница
    библиотеки и так показывает их полностью — второй «короткий» эндпоинт был
    бы лишней сущностью."""

    slug: str
    name: str
    version: int
    aliases: list[str] = Field(default_factory=list)
    tagline: str = ""
    archived: bool = False
    updated_by: str = ""
    updated_at: str | None = None
    block1: str = ""
    block2: str = ""
    block3: str = ""


class OutcomeIn(BaseModel):
    """Отметка исхода на экране результата — единственный вход в слой опыта."""

    outcome: Literal["shipped", "rejected"]
    comment: str = Field(default="", max_length=2000)


class RoleIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    # PUT заменяет состояние целиком: дефолт превратил бы забытое поле в тихое
    # разжалование с ответом 200, поэтому оба поля обязательны — лучше 422.
    role: Literal["admin", "user"]
    kb_editor: bool


class RoleOut(BaseModel):
    email: str
    role: str
    kb_editor: bool
