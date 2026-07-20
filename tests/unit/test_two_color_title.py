"""Two-color title (M4-web, 2026-07-15).

The dark webinar formats (1080x1080, stories, youtube covers) render the title
in two colors: the product name up to and including the first colon in white,
the tagline after it in Cloud.ru green. Figma authors this with two <span>s;
at runtime the title is a single user string, so the composer splits it on the
first ':' (inclusive → primary; remainder → accent).

Contract:
  1. TextLayer.accent_color set + text contains ':' → glyphs up to and including
     the colon use ``color``, the rest use ``accent_color``.
  2. No accent_color, or no ':' in the text → whole string uses ``color``
     (unchanged behavior).
  3. The split survives word-wrapping across multiple lines.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from infra.composer import compose
from infra.template_manifest import TemplateSpec

REPO_ROOT = Path(__file__).resolve().parents[2]

_WHITE = (0xFF, 0xFF, 0xFF)
_GREEN = (0x26, 0xD0, 0x7C)


def _spec(layer_extra: dict) -> TemplateSpec:
    base = {
        "type": "text",
        "slot": "title",
        "x": 0,
        "y": 0,
        "width": 600,
        "height": 200,
        "font_family": "SBSansDisplay",
        "font_weight": "Semibold",
        "font_size_max": 40,
        "font_size_min": 40,
        "line_height": 1.1,
        "color": "#FFFFFF",
        "align_h": "left",
        "align_v": "top",
        "max_lines": 4,
    }
    base.update(layer_extra)
    return TemplateSpec.model_validate(
        {"width": 600, "height": 200, "background_color": "#222222", "layers": [base]}
    )


def _has_color(img: Image.Image, rgb) -> bool:
    return any(
        img.getpixel((x, y))[:3] == rgb
        for x in range(0, img.width, 3)
        for y in range(0, img.height, 3)
    )


def test_accent_split_colors_before_and_after_colon():
    spec = _spec({"accent_color": "#26D07C"})
    img = compose(spec, texts={"title": "Продукт: остальное описание"}, assets_root=REPO_ROOT)
    assert _has_color(img, _WHITE), "primary (pre-colon) white glyphs missing"
    assert _has_color(img, _GREEN), "accent (post-colon) green glyphs missing"


def test_no_accent_color_is_single_color():
    spec = _spec({})  # no accent_color
    img = compose(spec, texts={"title": "Продукт: остальное описание"}, assets_root=REPO_ROOT)
    assert _has_color(img, _WHITE)
    assert not _has_color(img, _GREEN)


def test_accent_but_no_colon_is_single_color():
    spec = _spec({"accent_color": "#26D07C"})
    img = compose(spec, texts={"title": "Заголовок без двоеточия"}, assets_root=REPO_ROOT)
    assert _has_color(img, _WHITE)
    assert not _has_color(img, _GREEN)


def test_split_survives_multiline_wrap():
    # long tagline forces the accent run to wrap onto its own lines
    spec = _spec({"accent_color": "#26D07C", "font_size_max": 28, "font_size_min": 28})
    img = compose(
        spec,
        texts={"title": "Managed BI: все возможности BI-сервиса в облаке для команд"},
        assets_root=REPO_ROOT,
    )
    assert _has_color(img, _WHITE)
    assert _has_color(img, _GREEN)
    # the accent run wraps below the first line (line pitch ~31px), so green
    # must appear beneath it — proving the split flows through the wrap.
    below_first_line = img.crop((0, 35, 600, 200))
    assert _has_color(below_first_line, _GREEN)
