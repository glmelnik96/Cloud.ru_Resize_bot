"""generate_message_candidates node — the MARKETER proposes (GLM-4.7, thinking OFF).

Two parallel calls of 12 propositions each = 24 drafts (2026-07-28). Until now
the node produced exactly 12 and the next node "ranked" those same 12, so the
whole set shipped no matter how weak its bottom half was — a selection stage
with nothing to reject. The marketer now over-produces and the target audience
(``select_by_persona``) keeps the 12 that actually land.

The two calls must not converge, or 24 drafts are just 12 with paraphrases. So
they differ in two ways that change the CONTENT, not the wording:

  - a creative stance — batch A writes from the problem as it is lived today,
    batch B writes from the outcome and answers the objections;
  - a primary anchor slice — the persona's pains/motivations/objections are
    dealt alternately between the batches, so each starts from different
    material. Both batches still see the full persona: the slices are usually
    5-6 anchors each, and starving a batch would just make it repeat itself.

Input:
  - GraphState.brief (AdBrief) — emotion, notes, tone hints
  - GraphState.product (ProductBrief) — facts the copy must not exceed
  - GraphState.personas (list[Persona]) — single persona; personas[0] is used
Output:
  - GraphState.candidates (list[MessageCandidate]) — 24 drafts
"""

from __future__ import annotations

import asyncio

import structlog

from graph.agent_runner import run_agent
from graph.nodes.context import (
    NONE,
    experience_block,
    get_product,
    must_honour_block,
    notes_block,
    product_block,
)
from graph.prompts import extract_section, load_skill, render
from graph.state import AdBrief, CandidateSet, GraphState, Persona

log = structlog.get_logger(__name__)

_AGENT_ID = "generate_message_candidates"
_SKILL_NAME = "creative_ads_explorer"

_BATCH_DIRECTIVES = (
    (
        "Заходи ОТ ПРОБЛЕМЫ. Начинай с того, как человек живёт с этой болью "
        "сегодня: что он делает руками, чего ждёт, что теряет. Слоган должен "
        "попасть в узнавание «это про меня», и только потом обещать выход."
    ),
    (
        "Заходи ОТ РЕЗУЛЬТАТА И ВОЗРАЖЕНИЙ. Начинай с картины «как станет» — "
        "что человек сможет делать и чего больше не будет делать — и снимай "
        "его сомнения. Слоган должен звучать как уже достигнутое состояние."
    ),
)


async def generate_message_candidates(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("generate_message_candidates: state.personas is empty")
    persona = _coerce(personas_raw[0], Persona, "persona")
    product = get_product(state)
    session_id = state.get("session_id")

    skill = load_skill(_SKILL_NAME)
    system_tpl = extract_section(skill.body, "## System message")
    user_tpl = extract_section(skill.body, "## User message template")

    # Секция опыта живёт ОТДЕЛЬНО от user-шаблона: при пустой библиотеке
    # промпт обязан остаться байт-в-байт прежним, а пустой заголовок внутри
    # шаблона учил бы модель, что раздел необязателен.
    experience = experience_block(state)
    experience_tpl = (
        extract_section(skill.body, "## Experience addendum") if experience else ""
    )

    anchors = _anchor_slices(persona)
    batches = await asyncio.gather(
        *(
            _run_batch(
                system_tpl,
                user_tpl,
                brief,
                persona,
                product,
                directive=directive,
                anchors=slice_,
                experience=experience,
                experience_tpl=experience_tpl,
                session_id=session_id,
            )
            for directive, slice_ in zip(_BATCH_DIRECTIVES, anchors)
        )
    )

    candidates = [c.model_dump() for batch in batches for c in batch]
    log.info(
        "generate_candidates_ok",
        session_id=session_id,
        n=len(candidates),
        n_batches=len(batches),
        hooks=[c["hook_angle"] for c in candidates],
    )
    return {"candidates": candidates}


async def _run_batch(
    system_tpl: str,
    user_tpl: str,
    brief: AdBrief,
    persona: Persona,
    product,
    *,
    directive: str,
    anchors: list[str],
    experience: str,
    experience_tpl: str,
    session_id: str | None,
) -> list:
    system_msg = render(system_tpl, batch_directive=directive)
    user_msg = render(
        user_tpl,
        **{
            "product": product.canonical_name if product else brief.product,
            "brief.emotion": brief.emotion or NONE,
            "tone_hints_or_none": brief.tone_hints or NONE,
            "product_block": product_block(product),
            "must_honour_block": must_honour_block(product),
            "notes_block": notes_block(brief),
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.motivations": ", ".join(persona.motivations),
            "persona.objections": ", ".join(persona.objections),
            "persona.communication_style": persona.communication_style,
            "batch_anchors": "\n".join(f"- {a}" for a in anchors),
        },
    )
    if experience and experience_tpl:
        user_msg += "\n\n" + render(experience_tpl, experience_block=experience)
    result = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=CandidateSet,
        session_id=session_id,
    )
    return result.candidates


def _anchor_slices(persona: Persona) -> tuple[list[str], list[str]]:
    """Deal the persona's anchors alternately into two starting sets.

    Alternating (rather than cutting the list in half) keeps each batch's slice
    mixed across pains, motivations and objections — a straight split would
    hand one batch every pain and the other every objection.
    """
    labelled = [
        *(f"боль: {p}" for p in persona.pain_points),
        *(f"мотивация: {m}" for m in persona.motivations),
        *(f"возражение: {o}" for o in persona.objections),
    ]
    return labelled[::2], labelled[1::2]


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
