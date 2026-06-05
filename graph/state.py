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
        description="awareness | consideration | conversion | engagement | retention"
    )
    audience_raw: str = Field(description="Сырое описание ЦА от маркетолога")
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
    """Wrapper for derive_persona structured output (1-3 personas)."""

    personas: list[Persona] = Field(min_length=1, max_length=3)


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
    """Wrapper for generate_message_candidates structured output."""

    candidates: list[MessageCandidate] = Field(min_length=3, max_length=5)


# ----- Persona-loop verdict -------------------------------------------------


class Verdict(BaseModel):
    """Persona-as-TA reaction to a single candidate. NOT a critic — a simulated consumer."""

    candidate_id: str
    persona_segment: str
    resonance: int = Field(ge=0, le=10)
    clarity: int = Field(ge=0, le=10)
    action_intent: int = Field(ge=0, le=10)
    free_form_reaction: str
    main_friction: str | None = None


# ----- A/B mechanic ---------------------------------------------------------


class PriorVariant(BaseModel):
    """State passed to variant B run so it can diverge from A.

    Diversity required on >=2 of 3 axes.
    """

    slogan: str
    hook_angle: str
    brand_variant: str | None = None
    persona_priority: int = 0


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
    """One image produced by Phygital+ (or stub) — used as hero in Figma templates.

    url is filled later by infra/http_server.py + Cloudflare Tunnel (Direction 2):
    Figma Make's createImageAsync needs a public URL, not a local path.
    """

    url: str | None = None
    local_path: str
    style: str
    variant: str = "default"
    prompt: str = ""


# ----- LangGraph state ------------------------------------------------------


class GraphState(TypedDict, total=False):
    """Mutable graph state. Each node returns partial dict to merge."""

    session_id: str
    user_id: int
    raw_brief: str
    brief: AdBrief
    personas: list[Persona]
    persona_priority: int  # index into personas[] — 0 for A, 1 for B if >1
    candidates: list[MessageCandidate]
    verdicts: list[Verdict]
    candidate_scores: dict[str, float]
    winner: MessageCandidate | None
    revise_round: int
    prior_variant: PriorVariant | None
    text_approved: bool
    refine_comment: str | None
    # ----- Image stage (M3) -------------------------------------------------
    image_style: str  # photo | render | isometric — chosen by route_image_style
    image: dict | None  # GeneratedImage.model_dump()
    image_revise_round: int
    image_approved: bool | None
    image_refine_comment: str | None
    # ----- Render stage (M3) ------------------------------------------------
    rendered_files: list[dict]  # [{"format": "tg_post_1080x1350", "path": "/data/..."}, ...]
    rendered_zip_path: str | None
    cancelled: bool
    error: str | None
