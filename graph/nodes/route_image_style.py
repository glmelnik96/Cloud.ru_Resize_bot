"""route_image_style node — GLM-4.7 thinking-OFF classifier.

Input:
  - GraphState.brief (AdBrief)
  - GraphState.personas (list[Persona]) — single persona, [0]
  - GraphState.ranked (all 12 propositions, best-first)
Output:
  - GraphState.scenarios (list[str], one ∈ {render, photo} per proposition)
  - GraphState.image_style (str) — the top-ranked scenario, kept so the HITL
    image gate has a single value to display.

The 12-banner redesign (2026-06-21) routes EACH proposition into a brand
scenario, not just the winner. The brand book has only two banner scenarios:
``render`` (isometric 3D device cutout on the green-framed panel) and
``photo`` (full-bleed photo with a legibility scrim). The old ``isometric``
style folds into ``render`` — App1's render scenario already produces the
isometric device render — so the classifier's isometric verdict maps to
render. Anything unrecognised falls back to photo.

Purpose: routing only, not prompt engineering. See AGENTS.md §4 /
prompts/route_image_style.md for the style vocabulary.
"""

from __future__ import annotations

import asyncio

import structlog

from graph.agent_runner import run_agent
from graph.nodes import ranked_candidates
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    GraphState,
    ImageStyleChoice,
    MessageCandidate,
    Persona,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "route_image_style"
_SKILL_NAME = "route_image_style"

# The 12-banner set is LOCKED to a strict even split: exactly half render, half
# photo. The art-director classifier skews hard to render for technical
# audiences (render = isometric device render) — left unchecked it routes all 12
# to one scenario. Глеб's decision (2026-06-21): force a 6/6 lock so every set
# carries both looks. The classifier's per-candidate verdicts still decide WHICH
# propositions land in each half (by rank); the split count is fixed.
def _force_even_split(scenarios: list[str]) -> list[str]:
    """Force exactly ``len // 2`` render scenarios, the rest photo, keeping the
    classifier's preference by rank (scenarios are aligned to the best-first
    ranked order). If the classifier produced too many render, the worst-ranked
    extras flip to photo; too few, the best-ranked photos are promoted to
    render. An already-balanced split is returned untouched."""
    out = list(scenarios)
    target_render = len(out) // 2
    render_idx = [i for i, s in enumerate(out) if s == "render"]
    if len(render_idx) > target_render:
        # Too many render: keep the best-ranked target_render, flip the tail.
        for i in render_idx[target_render:]:
            out[i] = "photo"
    elif len(render_idx) < target_render:
        # Too few render: promote the best-ranked photos until the target.
        need = target_render - len(render_idx)
        photo_idx = [i for i, s in enumerate(out) if s == "photo"]
        for i in photo_idx[:need]:
            out[i] = "render"
    return out


def _to_scenario(raw: str) -> str:
    """Collapse the classifier's verdict into a brand banner scenario.

    isometric -> render (App1 render scenario IS the isometric device render);
    photo -> photo; anything else -> photo (safe default)."""
    style = (raw or "").strip().lower()
    if style in {"render", "isometric"}:
        return "render"
    if style == "photo":
        return "photo"
    return "photo"


async def route_image_style(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("route_image_style: state.personas is empty")
    personas = [_coerce(p, Persona, "persona") for p in personas_raw]
    persona = personas[0]

    candidates = ranked_candidates(state)
    session_id = state.get("session_id")

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    scenarios = await asyncio.gather(
        *(
            _classify_one(
                system_msg, user_tpl, brief, persona, cand, session_id
            )
            for cand in candidates
        )
    )
    raw_scenarios = list(scenarios)
    scenarios = _force_even_split(raw_scenarios)

    flipped = sum(1 for a, b in zip(raw_scenarios, scenarios) if a != b)
    log.info(
        "route_image_style_ok",
        session_id=session_id,
        n=len(scenarios),
        render=scenarios.count("render"),
        photo=scenarios.count("photo"),
        flipped_to_photo=flipped,
    )
    # image_style keeps the top-ranked CLASSIFIER pick (it's never flipped, since
    # flips start from the tail) so the HITL display matches the art direction.
    return {"scenarios": scenarios, "image_style": raw_scenarios[0]}


async def _classify_one(
    system_msg: str,
    user_tpl: str,
    brief: AdBrief,
    persona: Persona,
    cand: MessageCandidate,
    session_id: str | None,
) -> str:
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
            "winner.slogan": cand.slogan,
            "winner.body": cand.body,
            "winner.cta": cand.cta,
            "winner.hook_angle": cand.hook_angle,
        },
    )
    choice = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=ImageStyleChoice,
        session_id=session_id,
    )
    return _to_scenario(choice.style)


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
