"""generate_image_prompt node — per-message visual METAPHOR writer.

Metaphor-only redesign (2026-07-10): the downstream hero generator (App1)
runs its own brand enhancers — AMPLIFIERS that add ALL the styling (brand
green, materials, finish, lighting, film look) AND the anti-text guard. Our
old full prompts duplicated that work, so heroes came out varied-but-generic
and unrelated to the banner's message.

Now the LLM derives ONE concrete visual METAPHOR from THE MESSAGE itself
(slogan + body + hook_angle) — the idea made tangible — and the node appends
OUR fixed composition clause, the only thing the enhancer can't invent:
cutout positioning for render (isometric angle, near-square frame-filling
proportion, centered with even margins) and safe-crop framing for photo
(central subject, the frame is later cover-cropped to a tall vertical
banner).

Input:
  - GraphState.brief (AdBrief)
  - GraphState.personas (list[Persona]) — single persona, [0]
  - GraphState.ranked (all 12 propositions, best-first)
  - GraphState.scenarios (list[str], one ∈ {render, photo} per proposition)
Output:
  - GraphState.image_prompts (list[str]) — metaphor + composition clause per
    proposition, the per-banner generation input.
  - GraphState.image_prompt (str) — the top-ranked prompt (HITL display).

Soft validation: warn (don't retry) when a metaphor is outside the word band
or contains Cyrillic. No "no text" check — App1's enhancer owns that guard.
"""

from __future__ import annotations

import asyncio
import re

import structlog

from graph.agent_runner import run_agent
from graph.nodes import ranked_candidates
from graph.nodes.context import get_product
from graph.prompts import extract_section as _extract_section
from graph.prompts import load_skill
from graph.prompts import render as _render
from graph.state import (
    AdBrief,
    GraphState,
    ImageMetaphorOutput,
    MessageCandidate,
    Persona,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "generate_image_prompt"
_SKILL_NAME = "generate_image_prompt"

_VALID_STYLES = {"photo", "render"}

# OUR composition clauses — geometry/positioning ONLY. Styling (colour,
# materials, lighting, film look, brand green) and the anti-text guard belong
# to App1's brand enhancers; prescribing them here fights the enhancer.
#
# Render: the composer alpha-crops the cutout and scales it to fill a
# full-width band, so the object must be near-square and frame-filling
# (wide-flat or tall-thin letterboxes small), fully visible with even margins
# (an edge-cropped object can't be background-removed cleanly).
_COMPOSITION_RENDER = (
    "Show it as one single concrete three-dimensional object in an isometric "
    "view from a ~30-degree three-quarter angle, with compact, roughly square "
    "(about 1:1) overall proportions that fill the frame in both width and "
    "height, never wide-and-flat nor tall-and-thin. The one dominant subject, "
    "fully visible and centered with a small even margin on every side so "
    "nothing touches or is cropped by an edge."
)

# Photo: the composer cover-crops the frame into a tall 300x550 banner, so
# the subject must live in the central vertical band — anything near the
# left/right edges is discarded by the crop.
_COMPOSITION_PHOTO = (
    "A real, grounded documentary scene. Keep the main subject in the central "
    "vertical band of the frame with calm, uncluttered space above and below "
    "— the frame is later cover-cropped to a tall vertical banner, so nothing "
    "important should sit near the left or right edges."
)

_COMPOSITION = {"render": _COMPOSITION_RENDER, "photo": _COMPOSITION_PHOTO}

# What kind of metaphor we ask the LLM for, per scenario (goes into the LLM
# input, NOT into the wire prompt).
_METAPHOR_KIND = {
    "render": (
        "one single concrete three-dimensional OBJECT or device that makes "
        "this message's idea tangible — a recognisable object, not an "
        "abstract diagram"
    ),
    "photo": (
        "one real, believable documentary SCENE (a person, a place or a "
        "grounded object arrangement) that makes this message's idea "
        "tangible — not a contrived or surreal construction"
    ),
}

_WORD_MIN = 5
_WORD_MAX = 40
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def _load_sections() -> tuple[str, str]:
    """Load (system_msg, user_tpl) from the generate_image_prompt skill."""
    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")
    return system_msg, user_tpl


async def generate_image_prompt(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("generate_image_prompt: state.personas is empty")
    personas = [_coerce(p, Persona, "persona") for p in personas_raw]
    persona = personas[0]

    # Петля метафоры: если маркетолог оставил комментарий, перегенерируем
    # ТОЛЬКО образ победителя (1 LLM-вызов вместо N).
    comment = (state.get("metaphor_comment") or "").strip()
    if comment:
        return await _regenerate_winner(state, brief, persona, comment)

    candidates = ranked_candidates(state)
    product = get_product(state)
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
    styles = [s if s in _VALID_STYLES else "photo" for s in scenarios]

    system_msg, user_tpl = _load_sections()

    results = await asyncio.gather(
        *(
            _build_one(
                system_msg, user_tpl, brief, persona, product, cand, style, session_id
            )
            for cand, style in zip(candidates, styles, strict=False)
        )
    )
    prompts = [prompt for prompt, _meta in results]
    metaphor_meta = [meta for _prompt, meta in results]

    log.info(
        "generate_image_prompt_ok",
        session_id=session_id,
        n=len(prompts),
    )
    return {
        "image_prompts": prompts,
        "image_prompt": prompts[0],
        "metaphor_meta": metaphor_meta,
    }


async def _regenerate_winner(
    state: GraphState, brief: AdBrief, persona: Persona, comment: str
) -> dict:
    """Петля метафоры: перегенерировать ТОЛЬКО победителя (ranked[0]) с учётом
    русского комментария маркетолога. 1 LLM-вызов вместо N; остальные
    прописки и метаданные не трогаем."""
    candidates = ranked_candidates(state)
    product = get_product(state)
    session_id = state.get("session_id")

    scenarios = state.get("scenarios") or []
    style_raw = scenarios[0] if scenarios else "photo"
    style = style_raw if style_raw in _VALID_STYLES else "photo"

    prev_meta = (state.get("metaphor_meta") or [{}])[0]
    system_msg, user_tpl = _load_sections()

    prompt, meta = await _build_one(
        system_msg,
        user_tpl,
        brief,
        persona,
        product,
        candidates[0],
        style,
        session_id,
        prev_metaphor=prev_meta.get("metaphor", ""),
        feedback_comment=comment,
    )

    prompts = list(state.get("image_prompts") or [])
    metas = list(state.get("metaphor_meta") or [])
    if prompts:
        prompts[0] = prompt
    else:
        prompts = [prompt]
    if metas:
        metas[0] = meta
    else:
        metas = [meta]

    log.info("metaphor_regenerated", session_id=session_id)
    return {
        "image_prompts": prompts,
        "image_prompt": prompt,
        "metaphor_meta": metas,
        "metaphor_comment": None,
    }


async def _build_one(
    system_msg: str,
    user_tpl: str,
    brief: AdBrief,
    persona: Persona,
    product,
    cand: MessageCandidate,
    style: str,
    session_id: str | None,
    *,
    prev_metaphor: str = "",
    feedback_comment: str = "",
) -> tuple[str, dict]:
    user_msg = _render(
        user_tpl,
        **{
            "brief.product": product.canonical_name if product else brief.product,
            "product_what_it_is": product.what_it_is if product else "(not collected)",
            "brief.tone_hints": brief.tone_hints or "(none)",
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.communication_style": persona.communication_style,
            "message.slogan": cand.slogan,
            "message.body": cand.body,
            "message.cta": cand.cta,
            "message.hook_angle": cand.hook_angle,
            "message.anchor": cand.anchor or "(not stated)",
            "message.desired_outcome": cand.desired_outcome or "(not stated)",
            "metaphor_kind": _METAPHOR_KIND[style],
        },
    )
    if feedback_comment:
        prev_line = f"Previous metaphor: {prev_metaphor}\n" if prev_metaphor else ""
        user_msg += (
            "\n\nFEEDBACK FROM THE MARKETER (Russian) about the previous "
            f"metaphor — address it directly:\n{prev_line}"
            f"Comment: {feedback_comment}\n"
            "Propose a revised metaphor that honours this feedback — a different "
            "image unless the comment asks to keep or adjust the current one."
        )
    result = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=ImageMetaphorOutput,
        session_id=session_id,
    )
    metaphor = result.metaphor.strip()
    _soft_validate(metaphor, session_id=session_id)
    meta = {
        "candidate_id": cand.id,
        "metaphor": metaphor,
        "rationale": result.rationale,
        "intended_inference": result.intended_inference,
        "anti_reading": result.anti_reading,
    }
    return f"{metaphor.rstrip('.')}. {_COMPOSITION[style]}", meta


def _soft_validate(metaphor: str, *, session_id: str | None) -> None:
    words = _word_count(metaphor)
    if not (_WORD_MIN <= words <= _WORD_MAX):
        log.warning(
            "image_metaphor_length_out_of_band",
            session_id=session_id,
            words=words,
            target=f"{_WORD_MIN}-{_WORD_MAX}",
        )
    if _CYRILLIC_RE.search(metaphor):
        log.warning(
            "image_metaphor_contains_cyrillic",
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
