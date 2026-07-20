"""Inline row layer (M4-web, 2026-07-15): mixed text+image single-baseline flow.

The 2nd 1080x1080 header is ``Вебинар ↗ 30 июля в 11:00 ↗`` — per-run colours,
inline arrows, and date+time joined by "в". Contract:
  1. Text runs render in their own colour; an image run (the ↗ arrow PNG) is
     placed inline between them, left-to-right in declared order.
  2. Runs bound to slots pull runtime text; an empty slot is skipped but the row
     still flows (no crash, following runs still draw).
  3. align_h positions the whole block within the rect.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from infra.composer import compose
from infra.template_manifest import TemplateSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
_WHITE = (0xFF, 0xFF, 0xFF)
_GREEN = (0x26, 0xD0, 0x7C)
_ARROW_GREEN = (0x26, 0xD0, 0x7C)


def _spec(runs: list[dict], align_h: str = "left") -> TemplateSpec:
    layer = {
        "type": "inline_row",
        "x": 0,
        "y": 0,
        "width": 600,
        "height": 60,
        "font_family": "SBSansDisplay",
        "font_weight": "Semibold",
        "font_size": 30,
        "color": "#FFFFFF",
        "align_h": align_h,
        "align_v": "middle",
        "gap": 12,
        "runs": runs,
    }
    return TemplateSpec.model_validate(
        {"width": 600, "height": 60, "background_color": "#222222", "layers": [layer]}
    )


def _first_x(img: Image.Image, rgb) -> int | None:
    for x in range(img.width):
        for y in range(img.height):
            if img.getpixel((x, y))[:3] == rgb:
                return x
    return None


def test_runs_render_left_to_right_with_colours():
    spec = _spec(
        [
            {"kind": "text", "fixed_content": "Вебинар", "color": "#FFFFFF"},
            {"kind": "image", "path": "assets/brand/webinar/arrow_diag.png", "width": 31, "height": 31},
            {"kind": "text", "slot": "date", "color": "#26D07C"},
        ]
    )
    img = compose(spec, texts={"date": "30 июля"}, assets_root=REPO_ROOT)
    wx = _first_x(img, _WHITE)
    ax = _first_x(img, _ARROW_GREEN)
    assert wx is not None, "white text run missing"
    assert ax is not None, "arrow / green run missing"
    # white "Вебинар" comes before the arrow+green date
    assert wx < ax


def test_empty_slot_run_is_skipped_but_row_flows():
    spec = _spec(
        [
            {"kind": "text", "slot": "missing", "color": "#26D07C"},
            {"kind": "text", "fixed_content": "После", "color": "#FFFFFF"},
        ]
    )
    img = compose(spec, texts={}, assets_root=REPO_ROOT)
    # the following fixed run still draws
    assert _first_x(img, _WHITE) is not None


def test_align_h_right_pushes_block_to_the_right():
    runs = [{"kind": "text", "fixed_content": "X", "color": "#FFFFFF"}]
    left = compose(_spec(runs, "left"), texts={}, assets_root=REPO_ROOT)
    right = compose(_spec(runs, "right"), texts={}, assets_root=REPO_ROOT)
    assert _first_x(left, _WHITE) < _first_x(right, _WHITE)


def test_gap_before_overrides_row_gap_for_one_run():
    """Figma visual-family header: gap around arrows is 30, between words ~12.
    A run's gap_before replaces the row gap for the space preceding THAT run."""
    base = [
        {"kind": "text", "fixed_content": "A", "color": "#FFFFFF"},
        {"kind": "text", "fixed_content": "B", "color": "#26D07C"},
    ]
    wide = [
        {"kind": "text", "fixed_content": "A", "color": "#FFFFFF"},
        {"kind": "text", "fixed_content": "B", "color": "#26D07C", "gap_before": 40},
    ]
    img_base = compose(_spec(base), texts={}, assets_root=REPO_ROOT)
    img_wide = compose(_spec(wide), texts={}, assets_root=REPO_ROOT)
    # "A" starts at the same x (left aligned); "B" moves right by 40-12=28
    assert _first_x(img_base, _WHITE) == _first_x(img_wide, _WHITE)
    assert _first_x(img_wide, _GREEN) - _first_x(img_base, _GREEN) == 28
