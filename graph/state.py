"""LangGraph state schemas + Pydantic data classes.

GraphState — TypedDict for LangGraph (reducer-friendly).
Pydantic models — for LLM Structured Output validation and Redis serialization.
"""

from __future__ import annotations

import re
import uuid
from typing import TypedDict

from pydantic import BaseModel, Field, field_validator


# ----- Brief & inputs -------------------------------------------------------


class AdBrief(BaseModel):
    """What the marketer actually typed — assembled in code, not by an LLM.

    Until 2026-07-28 this was the output of a ``parse_brief`` LLM call that
    re-read a string the web layer had just concatenated from these very
    fields. The prompt then told the model to copy audience and emotion back
    out verbatim and to echo the format whitelist — a full round trip that
    could only lose information (it also compressed ``product`` to "1–6 слов",
    destroying detail the generator needs). The wizard fields are structured at
    the source, so the brief is now built directly and every field is the
    marketer's own wording.

    ``goal``/``channel``/``formats``/``age_rating`` are gone (2026-07-28,
    Глеб): goal and channel were LLM guesses nobody acted on, and the other
    two were never read by any node.
    """

    product: str = Field(description="Что рекламируем — слова маркетолога, дословно")
    audience_raw: str = Field(description="Описание ЦА — слова маркетолога, дословно")
    emotion: str = Field(
        default="",
        description=(
            "Чувство/образ, который должно вызвать предложение — формула "
            "'[чувство] + [образ/ассоциация]'. Драйвит генерацию предложений."
        ),
    )
    notes: str = Field(
        default="",
        description=(
            "Свободное поле: всё, что не влезло в остальные поля и требует "
            "ПРИСТАЛЬНОГО внимания — акции, ограничения, обязательные слова, "
            "контекст канала. Прокидывается во ВСЕ текстовые узлы."
        ),
    )
    source_url: str = Field(
        default="",
        description="Ссылка на страницу продукта/статью, которую читает пайплайн",
    )
    source_text: str = Field(
        default="",
        description="Текст, вычитанный из source_url (пусто, если ссылки не было)",
    )
    tone_hints: str | None = None


class ProductBrief(BaseModel):
    """What we actually sell — output of the ``understand_product`` node.

    The pipeline used to see only a product NAME, so a model that had never
    heard of "Evolution Managed RAG" either wrote generic copy or invented
    capabilities. This node fuses three grounding sources — the vendored
    knowledge base (``config/knowledge/``), the page behind ``source_url`` and
    the marketer's free-form ``notes`` — into a compact factual card that every
    downstream text node reads.

    ``must_honour`` is the teeth of the free-form field: instructions the
    marketer flagged as non-negotiable, lifted out so they can also be enforced
    mechanically as generation hooks rather than merely suggested in a prompt.

    Every list may come back empty. All three grounding sources are optional, so
    a product with no KB card, no link and no notes leaves the model nothing to
    honestly report — and the prompt orders it to invent nothing. A minimum-item
    floor here would only convert "no facts" into a hallucination or, as it did
    live on 2026-07-28, into a ValidationError that killed the run outright.
    """

    canonical_name: str = Field(description="Официальное название продукта")
    what_it_is: str = Field(
        min_length=20, description="1–2 предложения: что это, простыми словами"
    )
    key_capabilities: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Что реально умеет — факты, не обещания",
    )
    problems_solved: list[str] = Field(
        default_factory=list, max_length=8, description="Какие боли закрывает"
    )
    proof_points: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Конкретика, которую МОЖНО утверждать в рекламе (цифры, факты)",
    )
    vocabulary: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Термины продукта с короткой расшифровкой ('RAG — поиск по ...')",
    )
    must_honour: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Жёсткие требования из свободного поля: что обязательно/запрещено",
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


# CJK unified ideographs + kana. Used to catch Chinese leaking out of GLM.
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


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
    # Контракт-lite оффера (2026-08-07, этап 1 «Язык», синтез с Bannerzila).
    # Семантика их OfferSpec.pain/benefit в нашем формате: якорь у нас бывает
    # болью, мотивацией ИЛИ возражением — поэтому anchor, а не pain. Дефолты
    # пустые: parked checkpoints, записанные до полей, coerce'ятся на resume.
    anchor: str = Field(
        default="",
        description=(
            "Дословный якорь персоны, в который бьёт кандидат, с меткой вида "
            "«боль: …» / «мотивация: …» / «возражение: …»"
        ),
    )
    desired_outcome: str = Field(
        default="",
        description=(
            "Конкретный результат для человека («что станет»), выводимый из "
            "фактов о продукте — не обещание сверх границы достоверности"
        ),
    )


class DraftCandidate(MessageCandidate):
    """Generation-time constraints on top of MessageCandidate (2026-07-10).

    slogan is hard-capped at 42 chars — at the 300x600 slogan zone that is at
    most 3 lines at the full 30px, so the composer never has to shrink into the
    unreadable 20-22px floor. The cap lives HERE (not on MessageCandidate) so
    the LLM retry-with-feedback loop enforces it at generation, while parked
    checkpoints written before the cap still coerce fine on resume.

    anchor/desired_outcome are REQUIRED here (2026-08-07): the offer contract
    is enforced at generation via retry_with_feedback, base stays permissive.
    """

    slogan: str = Field(max_length=42)
    anchor: str = Field(min_length=3)
    desired_outcome: str = Field(min_length=10)

    @field_validator("slogan", "cta")
    @classmethod
    def _no_cjk(cls, value: str) -> str:
        """Reject CJK characters in text that goes onto the banner.

        GLM is a Chinese model and occasionally drops a Chinese word into
        otherwise fluent Russian (seen live 2026-07-28: «База знаний без运维»).
        The composer would render it verbatim, so it is caught here where
        retry_with_feedback can ask for a redo. Only slogan and cta are
        guarded — those are the fields that reach the layout; body is an
        internal working note.
        """
        if _CJK_RE.search(value):
            raise ValueError(
                "текст содержит иероглифы — пиши только по-русски: " f"{value!r}"
            )
        return value


class CandidateSet(BaseModel):
    """Wrapper for one generate_message_candidates call — 12 propositions.

    The node makes TWO such calls in parallel over disjoint persona anchors
    (2026-07-28), so the critic downstream picks 12 winners out of 24 drafts
    instead of rubber-stamping the only 12 that existed. Keeping the per-call
    size at 12 keeps each call inside a comfortable token budget and keeps the
    two batches genuinely independent.
    """

    candidates: list[DraftCandidate] = Field(min_length=12, max_length=12)


# ----- Selection (the persona judges the drafts) ----------------------------


class SelectedItem(BaseModel):
    """One pick of the persona-critic: which draft, how strongly, and why."""

    candidate_id: str
    score: float = Field(ge=0, le=10, description="Насколько сильно цепляет, 0..10")
    reason: str = Field(description="Одна строка ОТ ПЕРВОГО ЛИЦА: почему цепляет меня")


class SelectionSet(BaseModel):
    """Wrapper for select_by_persona structured output.

    The old ``rank_candidates`` was an outside analyst re-sorting the same 12
    drafts it was handed — a ranking with nothing to reject. The critic now
    speaks AS the persona and chooses exactly 12 of the 24 drafts, best-first,
    which is what "маркетолог предлагает, ЦА выбирает" was meant to be all
    along. The whole selected set goes to HITL; there is no single winner.
    """

    selected: list[SelectedItem] = Field(min_length=12, max_length=12)


# ----- Image stage (M3) -----------------------------------------------------


class ImageStyleChoice(BaseModel):
    """Output of route_image_style — LLM classifier picks one visual style.

    Style vocabulary is fixed by Cloud.ru 2.0 brand book (see AGENTS.md §4).
    The 12-banner redesign (2026-06-21) collapsed the prompt to two output
    scenarios; legacy ``isometric`` is still accepted and folds into render via
    route_image_style._to_scenario:
    - photo: real photography / staged scene
    - render: 3D render, studio-lit object (isometric folds in here)
    """

    style: str = Field(description="photo | render (legacy: isometric -> render)")
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


class ImageMetaphorOutput(BaseModel):
    """Output of generate_image_prompt (metaphor-only redesign 2026-07-10).

    The LLM returns ONE concrete visual METAPHOR per proposition — the
    message's idea made tangible (an object for render, a documentary scene
    for photo). Styling (brand green, materials, lighting, film look) and the
    anti-text guard are added downstream by App1's brand enhancers; the fixed
    composition/cutout-positioning clause is appended by our node. `rationale`
    is logged only.
    """

    metaphor: str = Field(min_length=10)
    rationale: str
    # Контракт-lite метафоры (2026-08-07, семантика их MetaphorSpec).
    # Вербальный якорь у нас структурный: метафора выводится ИЗ слогана,
    # который всегда печатается на макете, — отдельное поле не нужно.
    intended_inference: str = Field(
        min_length=10,
        description="Что зритель должен ЗАКЛЮЧИТЬ, увидев метафору (одно EN-предложение)",
    )
    anti_reading: str = Field(
        min_length=10,
        description="Вероятное ЛОЖНОЕ прочтение метафоры (одно EN-предложение)",
    )


# ----- LangGraph state ------------------------------------------------------


class GraphState(TypedDict, total=False):
    """Mutable graph state. Each node returns partial dict to merge."""

    session_id: str
    user_id: int
    brief: AdBrief
    product: dict  # ProductBrief.model_dump() — grounding for every text node
    personas: list[Persona]
    # 24 drafts (2 generator batches x 12) — the pool the persona picks from.
    candidates: list[MessageCandidate]
    # select_by_persona output: the chosen 12, best-first; each item =
    # candidate dict (slogan/body/cta/hook_angle/id) merged with score + reason.
    ranked: list[dict]
    text_approved: bool
    # ----- Image stage (M3.3 — user-uploaded hero) --------------------------
    image_style: str  # photo | render — top-ranked scenario (HITL display)
    image_prompt: str | None  # EN prompt shown to the user (top-ranked)
    image: dict | None  # GeneratedImage.model_dump(), single manual-upload fallback
    # ----- 12-banner stage (2026-06-21) -------------------------------------
    # Per ranked-candidate, aligned by index with state.ranked:
    scenarios: list[str]  # render | photo for each of the 12 propositions
    image_prompts: list[str]  # one EN hero prompt per proposition
    # Per-proposition metaphor meta (2026-08-07): candidate_id, metaphor,
    # rationale, intended_inference, anti_reading — читается provenance
    # (блок 4) и будущим инверсионным судьёй; на wire-промпт не влияет.
    metaphor_meta: list[dict]
    generated_heroes: list[dict]  # GeneratedImage.model_dump() per proposition
    # ----- Render stage (M3) ------------------------------------------------
    rendered_files: list[dict]  # [{"format": "banner_300x250", "path": "/data/..."}, ...]
    rendered_zip_path: str | None
    cancelled: bool
    error: str | None
