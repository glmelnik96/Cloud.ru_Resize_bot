"""route_image_style node — GLM-5.1 thinking-OFF classifier.

Input:
  - GraphState.brief (AdBrief)
  - GraphState.personas (list[Persona]) — single persona, [0]
  - GraphState.ranked (top item = the proposition composed onto the hero)
Output:
  - GraphState.image_style (str ∈ {photo, render, isometric})

Purpose: pick visual style for hero-image. Not prompt engineering — only routing.
See AGENTS.md §4 / prompts/route_image_style.md for the three-style vocabulary.
"""

from __future__ import annotations

import structlog

from graph.agent_runner import run_agent
from graph.nodes import chosen_candidate
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    GraphState,
    ImageStyleChoice,
    Persona,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "route_image_style"
_SKILL_NAME = "route_image_style"


async def route_image_style(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("route_image_style: state.personas is empty")
    personas = [_coerce(p, Persona, "persona") for p in personas_raw]
    persona = personas[0]

    winner = chosen_candidate(state)

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    user_msg = _render(
        user_tpl,
        **{
            "brief.product": brief.product,
            "brief.goal": brief.goal,
            "brief.channel": brief.channel,
            "brief.tone_hints": brief.tone_hints or "(не задано)",
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.communication_style": persona.communication_style,
            "winner.slogan": winner.slogan,
            "winner.body": winner.body,
            "winner.cta": winner.cta,
            "winner.hook_angle": winner.hook_angle,
        },
    )

    choice = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=ImageStyleChoice,
        session_id=state.get("session_id"),
    )

    style = choice.style.strip().lower()
    if style not in {"photo", "render", "isometric"}:
        log.warning(
            "route_image_style_unknown_value",
            session_id=state.get("session_id"),
            got=choice.style,
            fallback="photo",
        )
        style = "photo"

    log.info(
        "route_image_style_ok",
        session_id=state.get("session_id"),
        style=style,
        rationale=choice.rationale,
    )
    return {"image_style": style}


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
