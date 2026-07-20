"""Manual hero framing for M4 webinar resizes (fit engine backend).

The web UI shows a fixed reference frame (рамка + grid + head line) and lets
the user drag/zoom their photo (speaker) or the App1 metaphor render (visual)
inside it. The placement is a :class:`Transform` in reference-frame units;
:func:`bake_frame` bakes it into a reference-size RGBA image that the composer
consumes verbatim — speaker formats via ``compose(hero_prefit=True)`` (the
baked window IS the object box), visual formats via the file-space ``fit:crop``
math (any square file works).

:func:`auto_transform` computes the server-side default placement (what the
UI starts from, and what is used when the user skips manual fitting): the
hero's alpha bbox is placed into a target box, centered horizontally.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

# Window of the Figma reference speaker (alpha bbox 269,331,1763,3000 in the
# designer's 2000x3000 frame). All 26 speaker cover boxes were derived against
# this visible-object rect, so a baked window of this aspect reproduces the
# reference composition exactly.
SPEAKER_FRAME = (1494, 2669)

# Visual metaphors: the family fit:crop math is file-space over a square file.
VISUAL_FRAME = (1024, 1024)
# Alpha bbox of the reference blade metaphor inside its 1024 square — default
# placement box for arbitrary App1 renders.
VISUAL_BOX = (211, 157, 813, 887)

_ALPHA_THRESHOLD = 32


@dataclass(frozen=True)
class Transform:
    """Hero placement in reference-frame units.

    ``scale``: hero pixel -> ref units multiplier; hero top-left lands at
    ``(x, y)`` in ref coords.
    """

    scale: float
    x: float
    y: float


def bake_frame(hero: Image.Image, ref_w: int, ref_h: int, t: Transform) -> Image.Image:
    """Render the hero into a transparent ref_w x ref_h canvas per ``t``."""
    if hero.mode != "RGBA":
        hero = hero.convert("RGBA")
    canvas = Image.new("RGBA", (ref_w, ref_h), (0, 0, 0, 0))
    new_w = max(1, round(hero.width * t.scale))
    new_h = max(1, round(hero.height * t.scale))
    resized = hero.resize((new_w, new_h), Image.LANCZOS)
    px, py = round(t.x), round(t.y)
    # PIL requires non-negative paste dest for alpha_composite: pre-crop.
    left, top = max(0, -px), max(0, -py)
    if left or top:
        resized = resized.crop((left, top, new_w, new_h))
        px, py = max(0, px), max(0, py)
    if px < ref_w and py < ref_h and resized.width > 0 and resized.height > 0:
        canvas.alpha_composite(resized, (px, py))
    return canvas


def _alpha_bbox(hero: Image.Image) -> tuple[int, int, int, int]:
    if "A" in hero.getbands():
        mask = hero.getchannel("A").point(
            lambda v: 255 if v >= _ALPHA_THRESHOLD else 0
        )
        bbox = mask.getbbox()
    else:
        bbox = hero.getbbox()
    return bbox if bbox else (0, 0, hero.width, hero.height)


def auto_transform(
    hero: Image.Image,
    box: tuple[int, int, int, int],
    *,
    mode: str = "contain",
    anchor_v: str = "center",
) -> Transform:
    """Default placement: hero's alpha bbox into ``box`` (x0, y0, x1, y1).

    ``contain`` fits the object fully inside the box, ``cover`` fills the box.
    Horizontally centered; ``anchor_v`` is ``"center"`` or ``"top"``.
    """
    bx0, by0, bx1, by1 = box
    box_w, box_h = bx1 - bx0, by1 - by0
    ox0, oy0, ox1, oy1 = _alpha_bbox(hero)
    obj_w, obj_h = ox1 - ox0, oy1 - oy0
    pick = min if mode == "contain" else max
    scale = pick(box_w / obj_w, box_h / obj_h)
    x = bx0 + (box_w - obj_w * scale) / 2 - ox0 * scale
    if anchor_v == "top":
        y = by0 - oy0 * scale
    else:
        y = by0 + (box_h - obj_h * scale) / 2 - oy0 * scale
    return Transform(scale=scale, x=x, y=y)
