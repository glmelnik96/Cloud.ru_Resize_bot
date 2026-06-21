"""generate_image_prompt node — GLM-5.1 hero-image prompt writer (M3.3).

Input:
  - GraphState.brief (AdBrief)
  - GraphState.personas (list[Persona]) — single persona, [0]
  - GraphState.ranked (all 12 propositions, best-first)
  - GraphState.scenarios (list[str], one ∈ {render, photo} per proposition) —
    set by route_image_style upstream
Output:
  - GraphState.image_prompts (list[str]) — one EN single-paragraph hero
    prompt per proposition, the per-banner generation input.
  - GraphState.image_prompt (str) — the top-ranked prompt, kept so the HITL
    image gate / manual-upload fallback still has one prompt to display.

The 12-banner redesign (2026-06-21) writes one hero prompt per proposition,
each cued by that proposition's scenario (render -> isometric 3D device on a
clean studio backdrop; photo -> full scene). We no longer auto-generate here;
heroes are produced by App1 in CreativesService.generate_decision.

Soft validation: we warn (don't retry) when a prompt is outside the
40-90 word band, contains Cyrillic, or is missing "no text"/"no letters".
The schema only enforces min_length=20 on the prompt.
"""

from __future__ import annotations

import asyncio
import re

import structlog

from graph.agent_runner import run_agent
from graph.nodes import ranked_candidates
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    GraphState,
    ImagePromptOutput,
    MessageCandidate,
    Persona,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "generate_image_prompt"
_SKILL_NAME = "generate_image_prompt"

_VALID_STYLES = {"photo", "render"}

# The downstream hero generator (App1 `render` scenario) produces a 3D product
# render, not an isometric line-art. To get the isometric LOOK the brand wants,
# we bake an explicit isometric viewpoint into the EN prompt itself for render
# banners. Photo stays a plain full-scene cue.
_STYLE_DIRECTIVE = {
    "render": (
        "render (a premium 3D product render in the Cloud.ru brand style: a "
        "single sleek matte-metal isometric module or platform shown from a "
        "~30-degree three-quarter angle, with one tasteful centerpiece of "
        "translucent emerald-green tinted glass geometric shapes resting on "
        "it; clean soft studio lighting on a plain backdrop so the background "
        "can be cut out — a recognisable object, not an abstract diagram)"
    ),
    "photo": (
        "photo (an authentic, real photograph of a confident real person in a "
        "genuine modern tech workplace — server room, office, or studio — "
        "naturally lit and human, not a staged abstract metaphor)"
    ),
}

_WORD_MIN = 40
_WORD_MAX = 90
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


async def generate_image_prompt(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("generate_image_prompt: state.personas is empty")
    personas = [_coerce(p, Persona, "persona") for p in personas_raw]
    persona = personas[0]

    candidates = ranked_candidates(state)
    session_id = state.get("session_id")

    scenarios = state.get("scenarios") or []
    if len(scenarios) != len(candidates):
        log.warning(
            "generate_image_prompt_scenarios_mismatch",
            session_id=session_id,
            n_scenarios=len(scenarios),
            n_candidates=len(candidates),
            fallback="photo",
        )
        scenarios = [
            scenarios[i] if i < len(scenarios) else "photo"
            for i in range(len(candidates))
        ]

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    prompts = await asyncio.gather(
        *(
            _build_one(
                system_msg, user_tpl, brief, persona, cand, scen, session_id
            )
            for cand, scen in zip(candidates, scenarios)
        )
    )
    prompts = list(prompts)

    log.info(
        "generate_image_prompt_ok",
        session_id=session_id,
        n=len(prompts),
    )
    return {"image_prompts": prompts, "image_prompt": prompts[0]}


async def _build_one(
    system_msg: str,
    user_tpl: str,
    brief: AdBrief,
    persona: Persona,
    cand: MessageCandidate,
    scenario: str,
    session_id: str | None,
) -> str:
    image_style = scenario if scenario in _VALID_STYLES else "photo"
    style_directive = _STYLE_DIRECTIVE[image_style]
    user_msg = _render(
        user_tpl,
        **{
            "brief.product": brief.product,
            "brief.goal": brief.goal,
            "brief.channel": brief.channel,
            "brief.tone_hints": brief.tone_hints or "(none)",
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.communication_style": persona.communication_style,
            "winner.slogan": cand.slogan,
            "winner.cta": cand.cta,
            "winner.hook_angle": cand.hook_angle,
            "image_style": style_directive,
        },
    )
    result = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=ImagePromptOutput,
        session_id=session_id,
    )
    prompt = result.prompt.strip()
    _soft_validate(prompt, session_id=session_id)
    return prompt


def _soft_validate(prompt: str, *, session_id: str | None) -> None:
    words = _word_count(prompt)
    if not (_WORD_MIN <= words <= _WORD_MAX):
        log.warning(
            "image_prompt_length_out_of_band",
            session_id=session_id,
            words=words,
            target=f"{_WORD_MIN}-{_WORD_MAX}",
        )
    if _CYRILLIC_RE.search(prompt):
        log.warning(
            "image_prompt_contains_cyrillic",
            session_id=session_id,
        )
    low = prompt.lower()
    if "no text" not in low and "no letters" not in low:
        log.warning(
            "image_prompt_missing_no_text_guard",
            session_id=session_id,
        )


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w])


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
