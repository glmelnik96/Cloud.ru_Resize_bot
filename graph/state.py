"""LangGraph state schemas + Pydantic data classes.

GraphState — TypedDict for LangGraph (reducer-friendly).
Pydantic models — for LLM Structured Output validation and Redis serialization.
"""

from __future__ import annotations

import uuid
from typing import TypedDict

from pydantic import BaseModel, Field


# ----- Brief & inputs -------------------------------------------------------


class AdBrief(BaseModel):
    """Normalized marketing brief, produced by parse_brief node from wizard text."""

    product: str = Field(description="Канонизированное название продукта/услуги")
    goal: str = Field(
        default="awareness",
        description=(
            "awareness | consideration | conversion | engagement | retention "
            "— выводится parse_brief из контекста брифа (явно не спрашивается)"
        ),
    )
    audience_raw: str = Field(description="Сырое описание ЦА от маркетолога")
    emotion: str = Field(
        default="",
        description=(
            "Чувство/образ, который должно вызвать предложение — формула "
            "'[чувство] + [образ/ассоциация]'. Драйвит генерацию 12 предложений."
        ),
    )
    channel: str = Field(description="tg_post | vk_ad | ig_story | yandex_promo | ...")
    formats: list[str] = Field(
        default_factory=list,
        description="Slugs шаблонов из config/templates.json, например banner_240x400",
    )
    tone_hints: str | None = None
    constraints: list[str] = Field(
        default_factory=list,
        description="Дисклеймеры, обязательные слова, запреты",
    )
    cta_preference: str | None = None
    age_rating: str = Field(
        default="0+",
        description="Возрастная маркировка: 0+ | 6+ | 12+ | 16+ | 18+",
    )


# ----- Persona --------------------------------------------------------------


class Persona(BaseModel):
    """Discrete target-audience persona derived from AdBrief.audience_raw."""

    segment: str = Field(description="Краткий ярлык сегмента")
    age_range: str
    pain_points: list[str]
    motivations: list[str]
    objections: list[str]
    communication_style: str


class PersonaSet(BaseModel):
    """Wrapper for derive_persona structured output.

    Audience is single, so exactly ONE persona is derived. The 12 message
    propositions are 12 distinct angles INTO this one persona (each anchored on
    a different pain/motivation/objection + the emotion), so a per-candidate
    persona selector would be redundant (decision 2026-06-21).
    """

    personas: list[Persona] = Field(min_length=1, max_length=1)


# ----- Message candidate ----------------------------------------------------


class MessageCandidate(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    slogan: str
    body: str
    cta: str
    hook_angle: str = Field(
        description=(
            "emotional | rational | social_proof | direct_benefit | "
            "fear_of_missing_out | curiosity | authority"
        ),
    )


class CandidateSet(BaseModel):
    """Wrapper for generate_message_candidates structured output.

    Exactly 12 propositions (decision 2026-06-21): 12 distinct angles into the
    single persona, each anchored on a different pain/motivation/objection plus
    the brief emotion and a varied hook_angle. The whole set is delivered to the
    user (HITL approves the set), so there is no single-winner selection.
    """

    candidates: list[MessageCandidate] = Field(min_length=12, max_length=12)


# ----- Ranking (single light ranker) ---------------------------------------


class RankingItem(BaseModel):
    """One line of the ranker output: a candidate id, its predicted resonance
    score and a one-line «почему зайдёт ЦА»."""

    candidate_id: str
    score: float = Field(ge=0, le=10, description="Предсказанный резонанс с ЦА, 0..10")
    reason: str = Field(description="Одна строка: почему этот угол зайдёт ЦА")


class RankingSet(BaseModel):
    """Wrapper for rank_candidates structured output.

    The ranker orders the 12 propositions by predicted resonance and attaches a
    one-line rationale to each (decision 2026-06-21). There is no single winner
    — the whole ordered set goes to HITL.
    """

    ranking: list[RankingItem] = Field(min_length=12, max_length=12)


# ----- Image stage (M3) -----------------------------------------------------


class ImageStyleChoice(BaseModel):
    """Output of route_image_style — LLM classifier picks one of three visual styles.

    Style vocabulary is fixed by Cloud.ru 2.0 brand book (see AGENTS.md §4):
    - photo: real photography / staged scene
    - render: 3D render, studio-lit object
    - isometric: flat/isometric vector illustration
    """

    style: str = Field(description="photo | render | isometric")
    rationale: str = Field(description="Короткое обоснование выбора стиля под бриф")


class GeneratedImage(BaseModel):
    """One hero PNG — in M3.3 always uploaded by the user (no auto-generation).

    Field names kept from the M3.0 schema for graph-state compatibility:
    - local_path: where the bot stored the uploaded file on disk
    - style: which Cloud.ru style was requested in the prompt (photo/render/isometric)
    - prompt: the EN image_prompt that was shown to the user
    """

    url: str | None = None
    local_path: str
    style: str
    variant: str = "default"
    prompt: str = ""


class ImagePromptOutput(BaseModel):
    """Output of generate_image_prompt — what we show the user in TG.

    `prompt` is an EN single-paragraph string ready to paste into an image
    generator (MJ, DALL-E, SDXL, etc). `rationale` is the LLM's 1-sentence
    note on why this composition fits the brief; we log it but don't show
    it to the user.
    """

    prompt: str = Field(min_length=20)
    rationale: str


# ----- LangGraph state ------------------------------------------------------


class GraphState(TypedDict, total=False):
    """Mutable graph state. Each node returns partial dict to merge."""

    session_id: str
    user_id: int
    raw_brief: str
    brief: AdBrief
    personas: list[Persona]
    candidates: list[MessageCandidate]
    # rank_candidates output: ordered best-first, each item = candidate dict
    # (slogan/body/cta/hook_angle/id) merged with score + reason.
    ranked: list[dict]
    text_approved: bool
    # ----- Image stage (M3.3 — user-uploaded hero) --------------------------
    image_style: str  # photo | render | isometric — chosen by route_image_style
    image_prompt: str | None  # EN prompt shown to the user (generate_image_prompt)
    image: dict | None  # GeneratedImage.model_dump(), filled after user upload
    # ----- Render stage (M3) ------------------------------------------------
    rendered_files: list[dict]  # [{"format": "banner_300x250", "path": "/data/..."}, ...]
    rendered_zip_path: str | None
    cancelled: bool
    error: str | None
