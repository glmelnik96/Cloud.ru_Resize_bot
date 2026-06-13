"""Lightweight hero-image prompt writer for /banner.

Unlike the /new pipeline (graph/nodes/generate_image_prompt.py), /banner has
no brief/persona/winner state — just a banner title typed by the user. This
module turns that title into a single English image-generation prompt the bot
shows in chat for the user to confirm or edit, then sends to Phygital.

One GLM-5.1 call. Brand rules mirror prompts/generate_image_prompt.md:
the image is the visual half only — no text, letters, logos, brand chrome or
lemon-green accents (the composer overlays those later).
"""

from __future__ import annotations

import re

import structlog

from llm.cloudru import ModelCall, ModelName, get_shared_client

log = structlog.get_logger(__name__)

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

_SYSTEM = """You are a senior art director at Cloud.ru writing a single English \
hero-image prompt for an advertising banner. You receive only the banner's \
Russian title (the campaign theme). Produce ONE single-paragraph English prompt \
a designer can paste straight into an image generator.

THE PROMPT IS FOR THE HERO IMAGE ONLY — NOT THE WHOLE AD
Logos, the title, CTA buttons and any brand chrome are added later by a separate \
composition stage. Describe ONLY the visual scene behind those overlays. Do not \
mention Cloud.ru, do not describe a logo or text panel, do not lay out a banner.

VISUALISE THE THEME, NOT A GENERIC SCENE
Anchor the image in a concrete object or situation that instantly reads as the \
theme's subject — a clean 3D still life that embodies the metaphor (a single \
server module, a glowing cartridge slotting into a blade, parallel rendering \
pipelines, etc). Never settle for "a developer at a laptop".

STYLE
{style_block}

HARD RULES
- No text, no letters, no numbers, no logos, no signage, no UI, no watermarks. \
End the prompt with: "no text, no letters, no logos, no watermarks".
- No lemon-green (#CFF500) accents, no green brand stripes, no Cloud.ru marks.
- No frame, no border, no banner layout — a continuous scene only.
- Muted neutral palette: light greys, soft matte studio surfaces.
- English only. No Russian words. No words: epic, cinematic, revolutionary, \
innovative, dramatic, hyperreal, surreal, dreamy.
{cutout_block}

OUTPUT: the prompt text only — one paragraph, 45-90 words, no quotes, no markdown."""

_STYLE_RENDER = (
    "Render style: a clean 3D still life, studio lighting, soft shadow, matte "
    "materials, subtle ambient occlusion. No people."
)
_STYLE_PHOTO = (
    "Photo style: real objects in a staged scene, natural soft light, shallow "
    "depth of field, 50mm lens, slight grain."
)
_CUTOUT_BLOCK = (
    "- The subject MUST be a single isolated object centered on a plain, seamless, "
    "evenly-lit light-grey background, with clear empty margins around it, so the "
    "background can be removed cleanly afterwards."
)
_COVER_BLOCK = (
    "- A full-bleed scene that works as a framed photo (it will be cropped to fit "
    "a rectangle, not cut out)."
)


async def generate_banner_image_prompt(
    title: str,
    *,
    cutout: bool,
    style: str = "render",
) -> str:
    """Return a single English hero-image prompt for the given banner title.

    Args:
        title: the banner's Russian title (campaign theme).
        cutout: True if the image will be background-removed (isolated subject
            on a plain background); False for a framed cover photo.
        style: "render" (default, clean 3D) or "photo".

    Raises whatever the LLM client raises; callers fall back to manual upload.
    """
    style_block = _STYLE_PHOTO if style == "photo" else _STYLE_RENDER
    cutout_block = _CUTOUT_BLOCK if cutout else _COVER_BLOCK
    system = _SYSTEM.format(style_block=style_block, cutout_block=cutout_block)

    client = get_shared_client()
    raw = await client.call(
        ModelCall(
            model=ModelName.GLM,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Banner title: {title}"},
            ],
            thinking=False,
            max_tokens=400,
            temperature=0.6,
        )
    )
    prompt = _clean(raw)
    if _CYRILLIC_RE.search(prompt):
        log.warning("banner_prompt_has_cyrillic", title=title[:60])
    log.info("banner_prompt_generated", title=title[:60], chars=len(prompt), cutout=cutout)
    return prompt


def _clean(raw: str) -> str:
    """Strip code fences / wrapping quotes / leading labels the model may add."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    # drop a leading "Prompt:" style label
    s = re.sub(r"^(prompt|image prompt)\s*[:\-]\s*", "", s, flags=re.IGNORECASE)
    if len(s) >= 2 and s[0] in "\"'«" and s[-1] in "\"'»":
        s = s[1:-1].strip()
    return s
