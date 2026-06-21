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
# banners.
#
# OBJECT POSITIONING IS PINNED IN THE PROMPT (not just the composer). The
# composer crops the cutout to its alpha bbox, but it cannot recover an object
# that App1 generated touching/cropped by a frame edge (Photoroom then slices
# it). So the render directive explicitly demands a single, fully-visible,
# centered object with generous even margins — that is what makes the 12 render
# cutouts come back consistently framed.
_STYLE_DIRECTIVE_RENDER = (
    "render (a premium 3D product render in the Cloud.ru brand style: a single "
    "large, chunky, roughly-square isometric module or platform shown from a "
    "~30-degree three-quarter angle, the dominant subject that fills most of the "
    "frame and reads big and bold. Resting on top of it is a BIG, bright, vivid "
    "emerald-and-lime green translucent faceted glass crystal centerpiece — the "
    "clear glowing focal hero of the image, large (about a third of the object) "
    "and unmistakably green, NEVER blue, grey or clear glass. Clean soft studio "
    "lighting on a plain seamless light backdrop. Keep only a small, even margin "
    "of empty background on every side so the whole object is fully visible and "
    "never cropped or touching an edge, letting the background be removed "
    "cleanly — a recognisable object, not an abstract diagram)"
)

# Photo banners must NOT all be the same stock "confident man in an office".
# We vary the scene per banner (demographics / framing / setting) and make ~1/3
# of them people-free (objects, workspaces, hardware) for subject variety. The
# variant is chosen deterministically by the photo's position so a run stays
# reproducible. Each people-free variant carries the literal marker
# "no people in the scene" (used by the composer-agnostic tests + as a clear
# cue to App1). None of the photo variants mention 3D / isometric / green.
_PHOTO_PEOPLE = (
    "photo (an authentic photograph of a calm operator standing with a coffee, "
    "back turned to a softly glowing wall of status screens in a control room; "
    "cool ambient light, seen from behind, unhurried — a quiet sense of "
    "everything being under control)",
    "photo (an authentic photograph of a single on-call engineer in a dim night "
    "operations room, face lit softly by monitor glow, relaxed and unworried; "
    "moody low light, mid-shot — the calm of reliable round-the-clock "
    "infrastructure)",
    "photo (an authentic close-up of a hand resting lightly on a laptop "
    "trackpad at the very edge of the frame, the person soft and out of focus "
    "behind; gentle daylight, shallow depth of field, 50mm — the feeling of "
    "control right at your fingertips)",
    "photo (an authentic close-up portrait of a woman in her late 20s in a "
    "modern startup loft, a calm confident half-smile looking just off-camera; "
    "warm window light, shallow depth of field, 50mm; real skin and texture)",
    "photo (an authentic photograph of a man in his late 20s at a standing desk "
    "in a bright open-plan office, unhurried and quietly focused; soft daylight "
    "from a window, mid-shot — a relaxed, in-control workday)",
)
_PHOTO_NO_PEOPLE = (
    "photo (an authentic photograph of a long data-center aisle of server racks "
    "vanishing into deep perspective, subtle status lights receding into the "
    "distance; cool ambient light, deep depth of field, and no people in the "
    "scene — a sense of vast, scalable capacity)",
    "photo (an authentic macro photograph of immaculately routed fibre-optic "
    "cables in clean parallel rows on a matte surface, every strand in perfect "
    "order; soft directional light, crisp detail with bokeh, and no people in "
    "the scene — the calm of a perfectly ordered system)",
    "photo (an authentic still-life of a calm, uncluttered desk at dawn — a "
    "closed laptop and a single coffee, soft first light across the surface; "
    "gentle daylight, shallow depth of field, and no people in the scene — a "
    "quiet, ready, in-control start)",
)


def _photo_directives(n_photos: int) -> list[str]:
    """Assign a distinct scene directive to each of the ``n_photos`` photo
    banners, making every 3rd one (position % 3 == 2) people-free — ~1/3 of the
    set. People and people-free variants are indexed by their own running
    counters so consecutive picks stay distinct. With the production 6/6 lock
    this yields exactly 2 people-free photos out of 6."""
    out: list[str] = []
    people_i = 0
    nopeople_i = 0
    for pos in range(n_photos):
        if pos % 3 == 2:
            out.append(_PHOTO_NO_PEOPLE[nopeople_i % len(_PHOTO_NO_PEOPLE)])
            nopeople_i += 1
        else:
            out.append(_PHOTO_PEOPLE[people_i % len(_PHOTO_PEOPLE)])
            people_i += 1
    return out

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

    directives = _resolve_directives(scenarios)

    prompts = await asyncio.gather(
        *(
            _build_one(
                system_msg, user_tpl, brief, persona, cand, directive, session_id
            )
            for cand, directive in zip(candidates, directives)
        )
    )
    prompts = list(prompts)

    log.info(
        "generate_image_prompt_ok",
        session_id=session_id,
        n=len(prompts),
    )
    return {"image_prompts": prompts, "image_prompt": prompts[0]}


def _resolve_directives(scenarios: list[str]) -> list[str]:
    """Map each per-banner scenario to its visual-style directive: render -> the
    fixed isometric/positioning directive; photo -> a varied scene directive
    (with ~1/3 people-free), assigned by the photo's position in the set so the
    12 photos don't collapse into one identical stock person."""
    n_photos = sum(1 for s in scenarios if (s if s in _VALID_STYLES else "photo") == "photo")
    photo_dirs = _photo_directives(n_photos)
    out: list[str] = []
    photo_cursor = 0
    for scen in scenarios:
        style = scen if scen in _VALID_STYLES else "photo"
        if style == "render":
            out.append(_STYLE_DIRECTIVE_RENDER)
        else:
            out.append(photo_dirs[photo_cursor])
            photo_cursor += 1
    return out


async def _build_one(
    system_msg: str,
    user_tpl: str,
    brief: AdBrief,
    persona: Persona,
    cand: MessageCandidate,
    style_directive: str,
    session_id: str | None,
) -> str:
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
