"""RectLayer primitive (2026-07-15): a bare filled/rounded rectangle.

The webinar banners are built on the Cloud.ru "stepped" green silhouette — a
main panel plus offset accent tabs — which carries no text of its own. TextLayer
can't render it (it bails on empty text), so a dedicated colored-panel primitive
is needed. Belongs to the ``hero`` layer group (brand furniture behind content).

Contract:
  1. RectLayer(color, radius) draws a solid (optionally rounded) rectangle at
     (x,y,width,height) in the given color, full alpha.
  2. Optional ``alpha`` makes it semi-transparent (alpha-composited).
  3. It participates in the ``only`` filter under the ``hero`` group.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from infra.composer import compose
from infra.template_manifest import RectLayer, TemplateSpec

REPO_ROOT = Path(__file__).resolve().parents[2]


def _spec(layers) -> TemplateSpec:
    return TemplateSpec.model_validate(
        {"width": 100, "height": 100, "background_color": "#222222", "layers": layers}
    )


def test_rect_layer_solid_fill():
    spec = _spec(
        [
            {
                "type": "rect",
                "x": 10,
                "y": 20,
                "width": 40,
                "height": 30,
                "color": "#26D07C",
            }
        ]
    )
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    # inside the rect → green
    assert img.getpixel((30, 35))[:3] == (0x26, 0xD0, 0x7C)
    # outside → background
    assert img.getpixel((5, 5))[:3] == (0x22, 0x22, 0x22)
    # right/below the rect → background
    assert img.getpixel((60, 60))[:3] == (0x22, 0x22, 0x22)


def test_rect_layer_rounded_corner_clipped():
    spec = _spec(
        [
            {
                "type": "rect",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
                "color": "#26D07C",
                "radius": 30,
            }
        ]
    )
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    # center is green
    assert img.getpixel((50, 50))[:3] == (0x26, 0xD0, 0x7C)
    # extreme corner is clipped by the radius → shows background
    assert img.getpixel((1, 1))[:3] == (0x22, 0x22, 0x22)


def test_rect_layer_semi_transparent_blends():
    # 50% green over #222222 background → blended, neither pure green nor bg
    spec = _spec(
        [
            {
                "type": "rect",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
                "color": "#26D07C",
                "alpha": 128,
            }
        ]
    )
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    r, g, b = img.getpixel((50, 50))[:3]
    assert (0x22, 0x22, 0x22) != (r, g, b)
    assert (0x26, 0xD0, 0x7C) != (r, g, b)
    # green channel pulled up from 0x22 toward 0xD0
    assert 0x60 < g < 0xC0


def test_rect_layer_stroke_only_outline():
    """A transparent-fill rect with a stroke draws a border-only box (the VC
    banner's 'Вебинар' badge: green interior, 2px black outline)."""
    spec = _spec(
        [
            {
                "type": "rect",
                "x": 20,
                "y": 20,
                "width": 40,
                "height": 40,
                "color": "#222222",
                "alpha": 0,
                "stroke_color": "#000000",
                "stroke_width": 4,
            }
        ]
    )
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    # border pixel (top edge) is the stroke color
    assert img.getpixel((40, 21))[:3] == (0, 0, 0)
    # left edge is stroke
    assert img.getpixel((21, 40))[:3] == (0, 0, 0)
    # interior stays the background (transparent fill let it through)
    assert img.getpixel((40, 40))[:3] == (0x22, 0x22, 0x22)


def test_rect_layer_in_hero_group_only_filter():
    spec = _spec(
        [
            {
                "type": "rect",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
                "color": "#26D07C",
            }
        ]
    )
    # brand-only artifact excludes the rect (hero group) → transparent canvas
    img = compose(spec, texts={}, assets_root=REPO_ROOT, only={"brand"})
    assert img.getpixel((50, 50))[3] == 0
    # hero-only artifact includes it
    img2 = compose(spec, texts={}, assets_root=REPO_ROOT, only={"hero"})
    assert img2.getpixel((50, 50))[:3] == (0x26, 0xD0, 0x7C)
