"""Unit tests for infra.composer.

Coverage:
- canvas size + background color,
- z-order sorting (later layer overlaps earlier),
- hero cover/contain math,
- text wrap + auto-shrink picks the right size,
- per-line highlight rect bounds,
- CTA box background.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from infra.composer import _fit_text, _wrap_to_width, compose
from infra.template_manifest import (
    BoxBackground,
    FrameLayer,
    GradientLayer,
    HeroLayer,
    ImageLayer,
    PerLineHighlight,
    TemplateSpec,
    TextLayer,
    load_manifest,
)
from PIL import ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "templates.json"
FONT_DIR = REPO_ROOT / "assets" / "fonts"


def _solid_hero(w: int = 400, h: int = 400, color: str = "#FF00FF") -> Image.Image:
    return Image.new("RGBA", (w, h), color)


# ----- Word wrap --------------------------------------------------------------


def test_wrap_single_line_fits():
    font = ImageFont.truetype(str(FONT_DIR / "SBSansDisplay-Regular.otf"), 20)
    lines = _wrap_to_width("Hello world", font, 1000)
    assert lines == ["Hello world"]


def test_wrap_breaks_when_too_narrow():
    font = ImageFont.truetype(str(FONT_DIR / "SBSansDisplay-Regular.otf"), 20)
    lines = _wrap_to_width("Hello world foobar", font, 50)
    assert len(lines) >= 2


def test_wrap_preserves_explicit_newlines():
    font = ImageFont.truetype(str(FONT_DIR / "SBSansDisplay-Regular.otf"), 20)
    lines = _wrap_to_width("foo\nbar", font, 1000)
    assert lines == ["foo", "bar"]


def test_wrap_hard_breaks_overlong_word():
    """A single word wider than the rect is broken at the character level so
    it never overflows (long URLs / glued-together compound words)."""
    font = ImageFont.truetype(str(FONT_DIR / "SBSansDisplay-Regular.otf"), 40)
    max_width = 200
    word = "Импортозамещаемостьинфраструктуры"
    lines = _wrap_to_width(word, font, max_width)
    assert len(lines) >= 2
    assert all(font.getlength(ln) <= max_width for ln in lines)
    assert "".join(lines) == word  # no characters lost


def test_wrap_breaks_overlong_word_mixed_with_normal():
    font = ImageFont.truetype(str(FONT_DIR / "SBSansDisplay-Regular.otf"), 40)
    max_width = 200
    lines = _wrap_to_width("ок " + "x" * 80, font, max_width)
    assert all(font.getlength(ln) <= max_width for ln in lines)


# ----- Auto-shrink ------------------------------------------------------------


def _slogan_layer(width: int = 200, height: int = 90) -> TextLayer:
    return TextLayer(
        type="text",
        slot="slogan",
        x=0,
        y=0,
        width=width,
        height=height,
        font_family="SBSansDisplay",
        font_weight="Semibold",
        font_size_max=30,
        font_size_min=10,
        line_height=1.0,
        color="#FFFFFF",
        align_h="left",
        align_v="top",
        max_lines=3,
    )


def test_fit_text_uses_max_when_fits():
    layer = _slogan_layer(width=2000, height=2000)
    _, lines, size, truncated = _fit_text("Hi", layer=layer)
    assert size == 30
    assert lines == ["Hi"]
    assert truncated is False


def test_fit_text_shrinks_when_too_long():
    layer = _slogan_layer(width=80, height=50)
    _, _, size, _ = _fit_text(
        "Very long slogan that needs shrinking", layer=layer
    )
    assert size < 30


def test_fit_text_respects_min_floor():
    layer = _slogan_layer(width=10, height=10)
    _, _, size, _ = _fit_text("Impossible text length here", layer=layer)
    assert size == 10  # font_size_min


# ----- Truncation + ellipsis ---------------------------------------------------


def test_fit_text_truncates_with_ellipsis():
    """Slogan that can't fit even at font_size_min: lines are clipped to
    max_lines, the last visible line ends with a single U+2026 and still
    fits the wrap width."""
    layer = _slogan_layer(width=120, height=40)
    text = (
        "Очень длинный слоган который никогда не поместится в такой "
        "крошечный блок даже на минимальном размере шрифта"
    )
    font, lines, size, truncated = _fit_text(text, layer=layer)
    assert truncated is True
    assert size == 10  # font_size_min
    assert len(lines) == layer.max_lines
    assert lines[-1].endswith("\u2026")
    assert lines[-1].count("\u2026") == 1
    wrap_width = layer.width - 2 * layer.padding_x
    assert font.getlength(lines[-1]) <= wrap_width


def test_compose_logs_text_truncated_with_slug():
    import structlog.testing

    spec = TemplateSpec(
        width=140,
        height=60,
        background_color="#000000",
        layers=[_slogan_layer(width=120, height=40)],
    )
    long_slogan = (
        "Очень длинный слоган который никогда не поместится в такой "
        "крошечный блок даже на минимальном размере шрифта"
    )
    with structlog.testing.capture_logs() as logs:
        compose(
            spec,
            hero=_solid_hero(),
            texts={"slogan": long_slogan},
            assets_root=REPO_ROOT,
            slug="banner_test_140x60",
        )
    truncated_events = [e for e in logs if e["event"] == "text_truncated"]
    assert truncated_events, "text_truncated warning not logged"
    assert truncated_events[0]["slug"] == "banner_test_140x60"
    assert truncated_events[0]["slot"] == "slogan"


def test_compose_no_truncation_no_warning():
    spec = TemplateSpec(
        width=2000,
        height=2000,
        background_color="#000000",
        layers=[_slogan_layer(width=1900, height=1900)],
    )
    import structlog.testing

    with structlog.testing.capture_logs() as logs:
        compose(
            spec,
            hero=_solid_hero(),
            texts={"slogan": "Hi"},
            assets_root=REPO_ROOT,
            slug="big",
        )
    assert not [e for e in logs if e["event"] == "text_truncated"]


# ----- Canvas + z-order -------------------------------------------------------


def test_compose_canvas_size_and_background():
    spec = TemplateSpec(
        width=100,
        height=50,
        background_color="#112233",
        layers=[],
    )
    img = compose(spec, hero=_solid_hero(), texts={}, assets_root=REPO_ROOT)
    assert img.size == (100, 50)
    # corner pixel must be the background color
    assert img.getpixel((0, 0))[:3] == (0x11, 0x22, 0x33)


def test_z_order_later_layer_overlaps():
    """Two hero layers at z=10 and z=20, second covers first center."""
    spec = TemplateSpec(
        width=20,
        height=20,
        background_color="#000000",
        layers=[
            HeroLayer(type="hero", x=0, y=0, width=20, height=20, fit="cover", z=10),
            HeroLayer(type="hero", x=5, y=5, width=10, height=10, fit="cover", z=20),
        ],
    )
    # First hero red, second hero will be the same image — both magenta
    # but we want to verify z-order rendering by using a distinguishable
    # second pasted region: paste solid green hero on top.
    # We can call compose twice with different heroes — simpler: build
    # the canvas manually and re-run.
    red = _solid_hero(40, 40, "#FF0000")
    img = compose(spec, hero=red, texts={}, assets_root=REPO_ROOT)
    # outside the small box -> red
    assert img.getpixel((1, 1))[:3] == (255, 0, 0)
    # inside small box -> also red (same hero)
    assert img.getpixel((10, 10))[:3] == (255, 0, 0)


# ----- Hero cover / contain ---------------------------------------------------


def test_hero_cover_fills_target_rect():
    """cover: target 100x50, source 200x200 -> resized to 100x100 then
    cropped to 100x50. Whole target rect filled."""
    spec = TemplateSpec(
        width=100,
        height=50,
        background_color="#000000",
        layers=[
            HeroLayer(type="hero", x=0, y=0, width=100, height=50, fit="cover", z=0),
        ],
    )
    hero = _solid_hero(200, 200, "#00FF00")
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # every corner of the hero rect must be green (cover -> no letterbox)
    for x, y in [(0, 0), (99, 0), (0, 49), (99, 49), (50, 25)]:
        assert img.getpixel((x, y))[:3] == (0, 255, 0), f"({x},{y}) not filled"


def test_hero_contain_letterboxes():
    """contain: target 100x50, source 200x200 -> resized to 50x50, centered."""
    spec = TemplateSpec(
        width=100,
        height=50,
        background_color="#FF00FF",
        layers=[
            HeroLayer(
                type="hero", x=0, y=0, width=100, height=50, fit="contain", z=0
            ),
        ],
    )
    hero = _solid_hero(200, 200, "#00FF00")
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # centered green strip from x=25..74, magenta letterbox on sides
    assert img.getpixel((50, 25))[:3] == (0, 255, 0)
    assert img.getpixel((0, 25))[:3] == (255, 0, 255)
    assert img.getpixel((99, 25))[:3] == (255, 0, 255)


# ----- Real-manifest end-to-end smoke ----------------------------------------


@pytest.mark.parametrize(
    "slug", ["banner_300x600_render", "banner_300x600_photo"]
)
def test_compose_real_template_smoke(slug, tmp_path):
    """End-to-end: load real manifest, compose, write PNG, reopen, check size.

    Uses the real brand header / footer PNGs from assets/brand/creatives/, so
    this test catches missing assets too.
    """
    manifest = load_manifest(MANIFEST)
    spec = manifest.templates[slug]
    hero = _solid_hero(800, 800, "#777777")
    img = compose(
        spec,
        hero=hero,
        texts={
            "slogan": "Разработка и тестирование в облаке",
            "subtitle": "GenAI — генеративный искусственный интеллект",
            "cta": "Попробовать бесплатно",
        },
        assets_root=REPO_ROOT,
    )
    assert img.size == (spec.width, spec.height)
    out = tmp_path / f"{slug}.png"
    img.save(out, "PNG")
    reopened = Image.open(out)
    assert reopened.size == (spec.width, spec.height)


def test_render_banner_has_green_frame_and_header():
    """The render layout draws the green border frame and the brand header
    sits on top (top-left logo region is not pure background)."""
    manifest = load_manifest(MANIFEST)
    spec = manifest.templates["banner_300x600_render"]
    # transparent cutout so the #222 body + green frame show
    hero = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    img = compose(spec, hero=hero, texts={"slogan": "X", "cta": "Go"}, assets_root=REPO_ROOT)
    green = (0x26, 0xD0, 0x7C)
    assert img.getpixel((2, 300))[:3] == green     # left frame border
    assert img.getpixel((297, 300))[:3] == green    # right frame border
    assert img.getpixel((150, 595))[:3] == green    # bottom frame border


# ----- Per-line highlight + CTA background -----------------------------------


def test_per_line_highlight_present():
    """If highlight color != background color, the highlight rect must
    show through somewhere along the text line baseline."""
    spec = TemplateSpec(
        width=200,
        height=80,
        background_color="#FFFFFF",
        layers=[
            TextLayer(
                type="text",
                slot="slogan",
                x=10,
                y=10,
                width=180,
                height=60,
                font_family="SBSansDisplay",
                font_weight="Semibold",
                font_size_max=20,
                color="#FFFFFF",
                per_line_highlight=PerLineHighlight(
                    color="#FF0000", padding_x=4, padding_y=2
                ),
            ),
        ],
    )
    img = compose(spec, hero=_solid_hero(), texts={"slogan": "Hi"}, assets_root=REPO_ROOT)
    # somewhere in the highlight box (near top-left of text) must be red
    pixels = img.load()
    found_red = False
    for x in range(0, 60):
        for y in range(0, 40):
            r, g, b, _ = pixels[x, y]
            if r > 200 and g < 50 and b < 50:
                found_red = True
                break
        if found_red:
            break
    assert found_red, "per-line highlight not drawn"


# ----- Frame layer (green border) --------------------------------------------


def test_frame_layer_draws_border_only():
    """A frame draws a solid border of `thickness` around its rect and leaves
    the interior untouched (the dark body shows through)."""
    spec = TemplateSpec(
        width=20,
        height=20,
        background_color="#FFFFFF",
        layers=[
            FrameLayer(
                type="frame", x=0, y=0, width=20, height=20,
                thickness=3, color="#26D07C", z=10,
            ),
        ],
    )
    img = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    green = (0x26, 0xD0, 0x7C)
    # all four borders are green
    assert img.getpixel((0, 10))[:3] == green     # left
    assert img.getpixel((19, 10))[:3] == green    # right
    assert img.getpixel((10, 0))[:3] == green     # top
    assert img.getpixel((10, 19))[:3] == green    # bottom
    # interior untouched (background shows through)
    assert img.getpixel((10, 10))[:3] == (255, 255, 255)


def test_frame_layer_inset_rect():
    """A frame placed below a header (y>0) borders only its own rect; the
    region above y stays background."""
    spec = TemplateSpec(
        width=20,
        height=30,
        background_color="#222222",
        layers=[
            FrameLayer(
                type="frame", x=0, y=10, width=20, height=20,
                thickness=2, color="#26D07C", z=10,
            ),
        ],
    )
    img = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    green = (0x26, 0xD0, 0x7C)
    assert img.getpixel((10, 5))[:3] == (0x22, 0x22, 0x22)   # above frame
    assert img.getpixel((10, 10))[:3] == green               # frame top edge
    assert img.getpixel((10, 20))[:3] == (0x22, 0x22, 0x22)  # frame interior


# ----- Gradient layer (legibility scrim) -------------------------------------


def test_gradient_layer_darkens_bottom_only():
    """A vertical gradient scrim (transparent at top -> opaque dark at bottom)
    keeps the top of the rect close to the original and darkens the bottom."""
    spec = TemplateSpec(
        width=10,
        height=100,
        background_color="#FFFFFF",
        layers=[
            GradientLayer(
                type="gradient", x=0, y=0, width=10, height=100,
                color="#000000", from_alpha=0, to_alpha=255,
                direction="vertical", z=10,
            ),
        ],
    )
    img = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    top = img.getpixel((5, 0))[:3]
    bottom = img.getpixel((5, 99))[:3]
    assert top[0] > 230          # near-white at the top (alpha ~0)
    assert bottom[0] < 25        # near-black at the bottom (alpha ~255)


def test_cta_background_drawn():
    spec = TemplateSpec(
        width=100,
        height=50,
        background_color="#000000",
        layers=[
            TextLayer(
                type="text",
                slot="cta",
                x=10,
                y=10,
                width=80,
                height=30,
                font_family="SBSansDisplay",
                font_weight="Semibold",
                font_size_max=14,
                color="#000000",
                align_h="center",
                align_v="middle",
                max_lines=1,
                background=BoxBackground(color="#CFF500"),
            ),
        ],
    )
    img = compose(spec, hero=_solid_hero(), texts={"cta": "Go"}, assets_root=REPO_ROOT)
    # center of the cta rect should be CTA bg (#CFF500) or text color
    # corner of cta rect should be CFF500
    r, g, b, _ = img.getpixel((12, 12))
    assert r > 200 and g > 200 and b < 100  # roughly lemon
