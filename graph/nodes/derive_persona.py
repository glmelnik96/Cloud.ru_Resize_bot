"""derive_persona node — GLM-5.1 thinking-OFF.

Input:  GraphState.brief (AdBrief)
Output: GraphState.personas (list[Persona]), persona_priority (default 0)
"""

from __future__ import annotations

import structlog

from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import AdBrief, GraphState, PersonaSet
from llm.cloudru import CloudRuClient, ModelCall, ModelName

log = structlog.get_logger(__name__)

_SKILL_NAME = "derive_persona"


async def derive_persona(state: GraphState) -> dict:
    brief = state.get("brief")
    if brief is None:
        raise ValueError("derive_persona: state.brief is missing — parse_brief did not run")
    if not isinstance(brief, AdBrief):
        brief = AdBrief.model_validate(brief)

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    tone_block = (
        f"TONE HINTS: {brief.tone_hints}" if brief.tone_hints else ""
    )
    user_msg = _render(
        user_tpl,
        **{
            "brief.product": brief.product,
            "brief.goal": brief.goal,
            "brief.channel": brief.channel,
            "brief.audience_raw": brief.audience_raw,
            "tone_hints_block": tone_block,
        },
    )

    client = CloudRuClient()
    cfg = skill.model_config.get("glm-5.1", {})
    persona_set = await client.call_structured(
        ModelCall(
            model=ModelName.GLM,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            thinking=bool(cfg.get("thinking", False)),
            max_tokens=int(cfg.get("max_tokens", 2500)),
            temperature=float(cfg.get("temperature", 0.5)),
        ),
        schema=PersonaSet,
    )
    log.info(
        "derive_persona_ok",
        session_id=state.get("session_id"),
        n_personas=len(persona_set.personas),
        segments=[p.segment for p in persona_set.personas],
    )
    return {
        "personas": [p.model_dump() for p in persona_set.personas],
        "persona_priority": state.get("persona_priority", 0),
    }
