"""derive_persona node — GLM-4.7 thinking-OFF.

Derives exactly ONE persona (audience is single; every message angle targets
it). The persona's anchors — pains, motivations, objections — are the raw
material for the entire text stage, so this node is given the two grounding
sources the marketer's one-line audience description lacks:

  - the knowledge base's «Блок 3. Описание аудитории», which lists the product's
    real segments and what each one wants — the marketer typically names one of
    them in passing ("архитекторы"), and the KB fills in the substance;
  - the free-form ``notes`` field, where audience detail that fits nowhere else
    tends to land.

Input:  GraphState.brief (AdBrief), GraphState.product (ProductBrief).
Output: GraphState.personas (list[Persona] of length 1).
"""

from __future__ import annotations

import structlog

from graph import knowledge
from graph.agent_runner import run_agent
from graph.nodes.context import NONE, get_product, notes_block, product_block
from graph.prompts import extract_section as _extract_section
from graph.prompts import load_skill
from graph.prompts import render as _render
from graph.state import AdBrief, GraphState, PersonaSet

log = structlog.get_logger(__name__)

_AGENT_ID = "derive_persona"
_SKILL_NAME = "derive_persona"


async def derive_persona(state: GraphState) -> dict:
    brief = state.get("brief")
    if brief is None:
        raise ValueError("derive_persona: state.brief is missing")
    if not isinstance(brief, AdBrief):
        brief = AdBrief.model_validate(brief)
    product = get_product(state)

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    doc = knowledge.find_product(
        product.canonical_name if product else "", brief.product, brief.notes
    )
    tone_block = (
        f"TONE HINTS: {brief.tone_hints}" if brief.tone_hints else ""
    )
    user_msg = _render(
        user_tpl,
        **{
            "brief.product": product.canonical_name if product else brief.product,
            "brief.emotion": brief.emotion or NONE,
            "brief.audience_raw": brief.audience_raw,
            "notes_block": notes_block(brief),
            "product_block": product_block(product),
            "kb_audiences_block": (
                doc.audiences
                if doc
                else "(в базе знаний нет карточки аудиторий по этому продукту)"
            ),
            "tone_hints_block": tone_block,
        },
    )

    persona_set = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=PersonaSet,
        session_id=state.get("session_id"),
    )
    log.info(
        "derive_persona_ok",
        session_id=state.get("session_id"),
        n_personas=len(persona_set.personas),
        segments=[p.segment for p in persona_set.personas],
    )
    return {
        "personas": [p.model_dump() for p in persona_set.personas],
    }
