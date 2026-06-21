"""generate_image_prompt node — GLM-5.1 hero-image prompt writer (M3.3).

Input:
  - GraphState.brief (AdBrief)
  - GraphState.personas (list[Persona]) — single persona, [0]
  - GraphState.ranked (top item = the proposition composed onto the hero)
  - GraphState.image_style (str ∈ {photo, render, isometric}) — set by
    route_image_style upstream
Output:
  - GraphState.image_prompt (str) — EN single-paragraph prompt shown to
    the user in TG so they can paste it into MJ / DALL-E / SDXL / Nano
    Banana and upload the result back.

This node replaces the M3.2 generate_image (Phygital) node. We no longer
auto-generate the hero — the user does it themselves in their own image
tool and uploads via hitl_image_upload.

Soft validation: we warn (don't retry) when the prompt is outside the
40-90 word band, contains Cyrillic, or is missing "no text"/"no letters".
The schema only enforces min_length=20 on the prompt.
"""

from __future__ import annotations

import re

import structlog

from graph.agent_runner import run_agent
from graph.nodes import chosen_candidate
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    GraphState,
    ImagePromptOutput,
    Persona,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "generate_image_prompt"
_SKILL_NAME = "generate_image_prompt"

_VALID_STYLES = {"photo", "render", "isometric"}
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

    winner = chosen_candidate(state)

    image_style = (state.get("image_style") or "").strip().lower()
    if image_style not in _VALID_STYLES:
        log.warning(
            "generate_image_prompt_bad_style",
            session_id=state.get("session_id"),
            got=image_style,
            fallback="photo",
        )
        image_style = "photo"

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

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
            "winner.slogan": winner.slogan,
            "winner.cta": winner.cta,
            "winner.hook_angle": winner.hook_angle,
            "image_style": image_style,
        },
    )

    result = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=ImagePromptOutput,
        session_id=state.get("session_id"),
    )

    prompt = result.prompt.strip()
    _soft_validate(prompt, session_id=state.get("session_id"))

    log.info(
        "generate_image_prompt_ok",
        session_id=state.get("session_id"),
        style=image_style,
        words=_word_count(prompt),
        rationale=result.rationale,
    )
    return {"image_prompt": prompt}


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
