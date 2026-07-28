"""select_by_persona node — the TARGET AUDIENCE picks (GLM-4.7, thinking OFF).

Replaces ``rank_candidates`` (2026-07-28). That node was an outside analyst
writing "почему зайдёт ЦА" about the same 12 drafts that were shipping
regardless — its prompt even said «пиши как аналитик про ЦА, а не как сама
персона». It could reorder, never reject.

Here the persona speaks in first person and keeps 12 of the marketer's 24
drafts. Half the pool is rejected by construction, which is the only thing that
makes the generator's over-production worth paying for. The persona also holds
the drafts against the product facts and the marketer's hard requirements, so a
pretty slogan promising something the product does not do gets dropped rather
than ranked.

Input:
  - GraphState.brief (AdBrief) — emotion
  - GraphState.product (ProductBrief) — facts to check promises against
  - GraphState.personas (list[Persona]) — single persona; personas[0] is used
  - GraphState.candidates (list[MessageCandidate]) — the 24 drafts
Output:
  - GraphState.ranked (list[dict]) — the chosen 12, best-first; each item is the
    candidate dict merged with `score` and `reason`. The key name is unchanged
    so every downstream node keeps working.
"""

from __future__ import annotations

import structlog

from graph.agent_runner import run_agent
from graph.nodes.context import (
    NONE,
    get_product,
    must_honour_block,
    product_block,
)
from graph.prompts import extract_section, load_skill, render
from graph.state import (
    AdBrief,
    GraphState,
    MessageCandidate,
    Persona,
    SelectionSet,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "select_by_persona"
_SKILL_NAME = "select_by_persona"

# How many propositions reach the user. One banner is composed per pick, so
# this is also the banner count.
KEEP = 12


async def select_by_persona(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    candidates_raw = state.get("candidates") or []
    if not candidates_raw:
        raise ValueError("select_by_persona: state.candidates is empty")
    candidates = [_coerce(c, MessageCandidate, "candidate") for c in candidates_raw]

    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("select_by_persona: state.personas is empty")
    persona = _coerce(personas_raw[0], Persona, "persona")
    product = get_product(state)
    session_id = state.get("session_id")

    skill = load_skill(_SKILL_NAME)
    system_msg = extract_section(skill.body, "## System message")
    user_tpl = extract_section(skill.body, "## User message template")

    cards_block = "\n".join(
        f"- id={c.id} | slogan: {c.slogan} | cta: {c.cta} | hook: {c.hook_angle}"
        f" | body: {c.body}"
        for c in candidates
    )

    user_msg = render(
        user_tpl,
        **{
            "product": product.canonical_name if product else brief.product,
            "brief.emotion": brief.emotion or NONE,
            "product_block": product_block(product),
            "must_honour_block": must_honour_block(product),
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.motivations": ", ".join(persona.motivations),
            "persona.objections": ", ".join(persona.objections),
            "persona.communication_style": persona.communication_style,
            "candidates_block": cards_block,
        },
    )

    result: SelectionSet = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=SelectionSet,
        session_id=session_id,
    )

    ranked = _merge(candidates, result)

    log.info(
        "select_by_persona_ok",
        session_id=session_id,
        n_pool=len(candidates),
        n_selected=len(ranked),
        order=[r["id"] for r in ranked],
    )
    return {"ranked": ranked}


def _merge(candidates: list[MessageCandidate], result: SelectionSet) -> list[dict]:
    """The persona's picks, best-first, topped up to exactly ``KEEP`` items.

    The schema already forces 12 entries, but nothing stops the model from
    repeating an id or inventing one. Those are dropped and the shortfall is
    filled from the unpicked drafts (score 0) — the banner stage composes one
    banner per item, so a short list would silently ship fewer banners.
    """
    by_id = {c.id: c for c in candidates}
    picked: list[dict] = []
    seen: set[str] = set()
    for item in sorted(result.selected, key=lambda r: r.score, reverse=True):
        cand = by_id.get(item.candidate_id)
        if cand is None or cand.id in seen:
            continue
        seen.add(cand.id)
        picked.append({**cand.model_dump(), "score": item.score, "reason": item.reason})

    if len(picked) < KEEP:
        log.warning(
            "select_by_persona_short",
            n_valid=len(picked),
            keep=KEEP,
            n_returned=len(result.selected),
        )
        for cand in candidates:
            if len(picked) >= KEEP:
                break
            if cand.id not in seen:
                seen.add(cand.id)
                picked.append({**cand.model_dump(), "score": 0.0, "reason": ""})
    return picked[:KEEP]


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
