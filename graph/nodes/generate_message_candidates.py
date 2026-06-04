"""generate_message_candidates node — GLM-5.1 thinking-OFF.

Input:
  - GraphState.brief (AdBrief)
  - GraphState.personas (list[Persona])
  - GraphState.persona_priority (int)
  - GraphState.prior_variant (PriorVariant | None) — for A/B variant B
  - GraphState.revise_round (int) — 0 for first pass
  - GraphState.verdicts (list[Verdict]) — only used when revise_round > 0
Output:
  - GraphState.candidates (list[MessageCandidate])
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
    PriorVariant,
    Verdict,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "generate_message_candidates"
_SKILL_NAME = "creative_ads_explorer"


async def generate_message_candidates(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("generate_message_candidates: state.personas is empty")
    personas = [_coerce(p, Persona, "persona") for p in personas_raw]
    priority = state.get("persona_priority", 0)
    persona = personas[min(priority, len(personas) - 1)]

    prior_variant_raw = state.get("prior_variant")
    prior_variant = (
        _coerce(prior_variant_raw, PriorVariant, "prior_variant")
        if prior_variant_raw is not None
        else None
    )

    revise_round = state.get("revise_round", 0)
    verdicts_raw = state.get("verdicts") or []
    verdicts = [_coerce(v, Verdict, "verdict") for v in verdicts_raw]

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")
    anti_bias_tpl = _extract_section(skill.body, "## A/B anti-bias addendum")
    revise_tpl = _extract_section(skill.body, "## Revise addendum")

    anti_bias_block = ""
    if prior_variant is not None:
        anti_bias_block = _render(
            anti_bias_tpl,
            **{
                "prior_variant.slogan": prior_variant.slogan,
                "prior_variant.hook_angle": prior_variant.hook_angle,
            },
        )

    revise_block = ""
    if revise_round > 0 and verdicts:
        frictions = [
            f"- candidate={v.candidate_id} persona={v.persona_segment}: {v.main_friction}"
            for v in verdicts
            if v.main_friction
        ]
        revise_block = _render(
            revise_tpl,
            revise_round=revise_round,
            frictions_bullets="\n".join(frictions) if frictions else "(нет явных friction'ов, поднимай action_intent)",
        )

    constraints_block = (
        "; ".join(brief.constraints) if brief.constraints else "(нет дополнительных)"
    )

    user_msg = _render(
        user_tpl,
        **{
            "brief.product": brief.product,
            "brief.goal": brief.goal,
            "brief.channel": brief.channel,
            "tone_hints_or_none": brief.tone_hints or "(не задано)",
            "constraints_block": constraints_block,
            "cta_preference_or_none": brief.cta_preference or "(на усмотрение)",
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.motivations": ", ".join(persona.motivations),
            "persona.objections": ", ".join(persona.objections),
            "persona.communication_style": persona.communication_style,
            "anti_bias_block": anti_bias_block,
            "revise_block": revise_block,
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
        revise_round=revise_round,
        is_b_variant=prior_variant is not None,
        hooks=[c.hook_angle for c in result.candidates],
    )
    return {"candidates": [c.model_dump() for c in result.candidates]}


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
