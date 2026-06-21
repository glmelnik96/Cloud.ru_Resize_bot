"""generate_message_candidates node — GLM-5.1 thinking-OFF.

Generates EXACTLY 12 propositions as 12 distinct angles into the SINGLE persona
(redesign 2026-06-21): each anchored on a different pain/motivation/objection
plus the brief emotion, with a varied hook_angle. No A/B variant, no revise
loop — the whole set is delivered to the user.

Input:
  - GraphState.brief (AdBrief) — incl. emotion
  - GraphState.personas (list[Persona]) — single persona; personas[0] is used
Output:
  - GraphState.candidates (list[MessageCandidate]) — 12 items
"""

from __future__ import annotations

import structlog

from graph.agent_runner import run_agent
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    CandidateSet,
    GraphState,
    Persona,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "generate_message_candidates"
_SKILL_NAME = "creative_ads_explorer"


async def generate_message_candidates(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("generate_message_candidates: state.personas is empty")
    persona = _coerce(personas_raw[0], Persona, "persona")

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    constraints_block = (
        "; ".join(brief.constraints) if brief.constraints else "(нет дополнительных)"
    )

    user_msg = _render(
        user_tpl,
        **{
            "brief.product": brief.product,
            "brief.goal": brief.goal,
            "brief.channel": brief.channel,
            "brief.emotion": brief.emotion or "(не задано)",
            "tone_hints_or_none": brief.tone_hints or "(не задано)",
            "constraints_block": constraints_block,
            "cta_preference_or_none": brief.cta_preference or "(на усмотрение)",
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.motivations": ", ".join(persona.motivations),
            "persona.objections": ", ".join(persona.objections),
            "persona.communication_style": persona.communication_style,
        },
    )

    result = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=CandidateSet,
        session_id=state.get("session_id"),
    )
    log.info(
        "generate_candidates_ok",
        session_id=state.get("session_id"),
        n=len(result.candidates),
        hooks=[c.hook_angle for c in result.candidates],
    )
    return {"candidates": [c.model_dump() for c in result.candidates]}


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
