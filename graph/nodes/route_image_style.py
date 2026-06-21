"""route_image_style node — GLM-5.1 thinking-OFF classifier.

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

# The 12-banner set must always carry SOME photo banners: the art-director
# classifier skews to render for technical audiences (render = isometric device
# render), which would route all 12 to render and lose the photo scenario the
# redesign requires. If fewer than MIN_PHOTO photos come back, the lowest-ranked
# render propositions are flipped to photo (top-ranked picks are preserved).
MIN_PHOTO = 4


def _ensure_min_photo(scenarios: list[str]) -> list[str]:
    """Guarantee at least MIN_PHOTO photo scenarios by flipping the worst-ranked
    render propositions (end of the best-first list) to photo. No-op when the
    classifier already produced enough photos, or when there aren't enough
    candidates to reach the quota."""
    out = list(scenarios)
    quota = min(MIN_PHOTO, len(out))
    if out.count("photo") >= quota:
        return out
    # Flip render -> photo from the tail (worst-ranked) until the quota is met.
    for i in range(len(out) - 1, -1, -1):
        if out.count("photo") >= quota:
            break
        if out[i] == "render":
            out[i] = "photo"
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
    scenarios = _ensure_min_photo(raw_scenarios)

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
