"""Texture layer (M4-web, 2026-07-15): the Cloud.ru arrow-grid motif.

The webinar panels carry a tiled line texture (diagonal + L-bracket per cell).
An ImageLayer only scales a PNG, so on a non-square region the cells stretch.
TextureLayer tiles one native-size cell across the rect. Contract:
  1. The motif draws strokes (partial coverage): some cells' pixels take the
     texture colour, but most of the rect stays background (it's thin lines).
  2. Square cells on a TALL region: the same colour appears in the top third
     AND the bottom third (proving vertical tiling, not one stretched cell).
  3. It's brand furniture -> the ``hero`` layer group (only-filter).
"""

from __future__ import annotations

from pathlib import Path

from infra.composer import compose
from infra.template_manifest import TemplateSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
_GREEN = (0x15, 0x9A, 0x57)


def _spec(layer_extra: dict, w: int, h: int) -> TemplateSpec:
    base = {"type": "texture", "x": 0, "y": 0, "width": w, "height": h, "color": "#159A57"}
    base.update(layer_extra)
    return TemplateSpec.model_validate(
        {"width": w, "height": h, "background_color": "#FFFFFF", "layers": [base]}
    )


def _count(img, rgb) -> int:
    return sum(
        img.getpixel((x, y))[:3] == rgb
        for x in range(0, img.width, 2)
        for y in range(0, img.height, 2)
    )


def test_texture_is_partial_line_coverage():
    spec = _spec({"cell": 66.0}, 300, 300)
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    green = _count(img, _GREEN)
    white = _count(img, (255, 255, 255))
    assert green > 0, "no texture strokes drawn"
    assert white > green, "texture should be thin lines, not fill the rect"


def test_texture_tiles_vertically_on_tall_region():
    # a tall strip like 1080x1350's 321x937 texture panel
    spec = _spec({"cell": 66.0}, 260, 780)
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    top = img.crop((0, 0, 260, 260))
    bottom = img.crop((0, 520, 260, 780))
    assert _count(top, _GREEN) > 0, "no texture in top third"
    assert _count(bottom, _GREEN) > 0, "no texture in bottom third (didn't tile down)"


def test_texture_strokes_are_antialiased():
    # Figma exports the motif with AA: fractional cell sizes (35.6347) put
    # strokes at subpixel positions, so edge pixels carry partial ink. A
    # hairline whole-pixel rendering (no blend pixels) reads visibly thinner than
    # the refs (M4 visual timepad, 2026-07-20).
    spec = _spec({"cell": 35.6347}, 300, 300)
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    blend = 0
    for x in range(0, img.width, 2):
        for y in range(0, img.height, 2):
            r, g, b = img.getpixel((x, y))[:3]
            if (r, g, b) != (255, 255, 255) and (r, g, b) != _GREEN:
                # between bg (white) and stroke colour on every channel
                if 0x15 < r < 255 and 0x9A < g < 255 and 0x57 < b < 255:
                    blend += 1
    assert blend > 20, f"expected anti-aliased stroke edges, found {blend} blend pixels"


def test_texture_in_hero_group():
    spec = _spec({"cell": 66.0}, 200, 200)
    img = compose(spec, texts={}, assets_root=REPO_ROOT, only={"hero"})
    assert _count(img, _GREEN) > 0
    # excluded from a message-only artifact
    img2 = compose(spec, texts={}, assets_root=REPO_ROOT, only={"message"})
    assert _count(img2, _GREEN) == 0
