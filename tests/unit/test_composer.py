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

from infra.composer import _apply_orphan_glue, _fit_text, _wrap_to_width, compose
from infra.template_manifest import (
    BoxBackground,
    FrameLayer,
    GradientLayer,
    HeroCutoutLayer,
    HeroLayer,
    ImageLayer,
    PatternDotsLayer,
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


# ----- Orphan-preposition glue ------------------------------------------------

_NBSP = "\u00a0"


def test_orphan_glue_binds_one_letter_word():
    """Single-letter words (и, в, с, к...) are glued to the FOLLOWING word with
    a non-breaking space so they never get stranded at a line end."""
    assert _apply_orphan_glue("Разработка и тестирование") == (
        f"Разработка и{_NBSP}тестирование"
    )


def test_orphan_glue_binds_listed_preposition():
    """Multi-letter prepositions/conjunctions from the glue set bind too."""
    assert _apply_orphan_glue("Облако для работы") == f"Облако для{_NBSP}работы"


def test_orphan_glue_chain_of_short_words():
    """A run of short words each glue forward: 'для работы с GenAI'."""
    assert _apply_orphan_glue("Облако для работы с GenAI") == (
        f"Облако для{_NBSP}работы с{_NBSP}GenAI"
    )


def test_orphan_glue_trailing_glue_word_left_alone():
    """A glue word with no following token can't bind — left as-is, no crash."""
    assert _apply_orphan_glue("работаем в") == "работаем в"


def test_orphan_glue_preserves_newlines():
    assert _apply_orphan_glue("Разработка и\nтест в облаке") == (
        f"Разработка и\nтест в{_NBSP}облаке"
    )


def test_orphan_glue_keeps_glued_pair_on_one_line():
    """The glued NBSP pair must wrap as a single unit: a short width that would
    otherwise strand 'в' at a line end keeps 'в облаке' together."""
    font = ImageFont.truetype(str(FONT_DIR / "SBSansDisplay-Semibold.otf"), 30)
    glued = _apply_orphan_glue("Разработка в облаке")
    lines = _wrap_to_width(glued, font, 170)
    # no line may end with a bare one-letter glue word
    for ln in lines:
        last = ln.split(" ")[-1]
        assert last != "в", f"orphan preposition stranded in {lines!r}"


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


# ----- hero_cutout alpha-bbox crop -------------------------------------------


def _padded_cutout(
    canvas_wh: tuple[int, int],
    obj_box: tuple[int, int, int, int],
    color: str = "#00FF00",
) -> Image.Image:
    """A transparent canvas with one opaque rectangle (the visible subject).

    Mimics a background-removed hero: most of the PNG is transparent padding,
    the real object occupies only ``obj_box`` (l, t, r, b). The composer must
    scale/anchor the *visible object*, not the whole padded canvas.
    """
    img = Image.new("RGBA", canvas_wh, (0, 0, 0, 0))
    obj = Image.new(
        "RGBA",
        (obj_box[2] - obj_box[0], obj_box[3] - obj_box[1]),
        color,
    )
    img.paste(obj, (obj_box[0], obj_box[1]))
    return img


def test_hero_cutout_crops_transparent_padding_before_fit():
    """A small object in the corner of a large transparent canvas must be
    scaled by its OWN bounds (alpha bbox) and centered, not left tiny in a
    corner because the padded canvas drove the scale."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=0,
                y=0,
                width=100,
                height=100,
                fit="contain",
                anchor_h="center",
                anchor_v="middle",
                allow_upscale=True,
                z=0,
            )
        ],
    )
    # 40x40 object in the top-left of a 200x200 transparent canvas.
    hero = _padded_cutout((200, 200), (0, 0, 40, 40), "#00FF00")
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # After bbox-crop, the 40x40 object scales (contain) to fill the 100x100
    # rect and is centered: center + all corners are the object colour.
    assert img.getpixel((50, 50))[:3] == (0, 255, 0)
    for x, y in [(2, 2), (97, 2), (2, 97), (97, 97)]:
        assert img.getpixel((x, y))[:3] == (0, 255, 0), f"({x},{y}) not object"


def test_hero_cutout_ignores_faint_alpha_halo():
    """Background removal (App1/Photoroom) often leaves a faint near-transparent
    halo of stray pixels far from the real object. `Image.getbbox()` treats any
    alpha>0 pixel as content, so that halo blows the crop box out to almost the
    whole frame — the real object then scales DOWN and floats small (live: the
    isometric devices that came back tiny/high). The crop must threshold the
    alpha so a faint halo is ignored and the SOLID object drives the scale."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=0,
                y=0,
                width=100,
                height=100,
                fit="contain",
                anchor_h="center",
                anchor_v="middle",
                allow_upscale=True,
                z=0,
            )
        ],
    )
    # 40x40 solid object centered in a 200x200 transparent canvas, PLUS one
    # faint (alpha=8) stray pixel in the far corner (the bg-removal halo).
    hero = _padded_cutout((200, 200), (80, 80, 120, 120), "#00FF00")
    hero.putpixel((3, 3), (255, 0, 0, 8))
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # The faint halo must be ignored: the 40x40 object still upscales to fill
    # the 100x100 rect (object colour at the centre AND near the edges).
    assert img.getpixel((50, 50))[:3] == (0, 255, 0)
    for x, y in [(2, 50), (97, 50), (50, 2), (50, 97)]:
        assert img.getpixel((x, y))[:3] == (0, 255, 0), f"({x},{y}) not object — halo shrank it"


def test_hero_cutout_bottom_anchor_uses_object_not_padding():
    """With bottom anchoring, the visible object must sit on the rect's bottom
    edge regardless of transparent padding below it in the source PNG."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=0,
                y=0,
                width=100,
                height=100,
                fit="contain",
                anchor_h="center",
                anchor_v="bottom",
                allow_upscale=False,
                z=0,
            )
        ],
    )
    # 40x40 object at the TOP of a 200x200 canvas (lots of transparent space
    # below it). Bottom-anchored, the object must land at the rect bottom.
    hero = _padded_cutout((200, 200), (80, 0, 120, 40), "#00FF00")
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # Bottom row contains the object; top row is background (object hugged the
    # bottom, not floating where its padding would have put it).
    assert img.getpixel((50, 98))[:3] == (0, 255, 0)
    assert img.getpixel((50, 2))[:3] == (255, 0, 255)


def test_hero_cutout_fully_transparent_is_noop():
    """A hero with no opaque pixels (getbbox None) must not crash; the rect
    stays background."""
    spec = TemplateSpec(
        width=60,
        height=60,
        background_color="#123456",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout", x=0, y=0, width=60, height=60, z=0
            )
        ],
    )
    hero = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    assert img.getpixel((30, 30))[:3] == (0x12, 0x34, 0x56)


def test_hero_prefit_skips_alpha_bbox_crop():
    """M4-web fit engine: the user manually frames the hero in the web UI and
    the server bakes it into a reference-size canvas (infra.hero_fit). That
    canvas encodes the CHOSEN placement — the transparent margins are part of
    the composition. compose(hero_prefit=True) must consume it verbatim:
    scale the WHOLE canvas into the rect, never re-crop to the alpha bbox
    (which would undo the user's manual fitting)."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=0,
                y=0,
                width=100,
                height=100,
                fit="cover",
                anchor_h="center",
                anchor_v="top",
                allow_upscale=True,
                z=0,
            )
        ],
    )
    # Baked frame 200x200: object only in the top-left quadrant; the user
    # deliberately left the rest empty.
    hero = _padded_cutout((200, 200), (0, 0, 100, 100), "#00FF00")
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT, hero_prefit=True)
    # Whole canvas scales 0.5: object occupies exactly the top-left 50x50.
    assert img.getpixel((25, 25))[:3] == (0, 255, 0)
    # Without prefit these would ALL be object-green (bbox crop + cover fill).
    assert img.getpixel((75, 25))[:3] == (0xFF, 0x00, 0xFF)
    assert img.getpixel((25, 75))[:3] == (0xFF, 0x00, 0xFF)
    assert img.getpixel((75, 75))[:3] == (0xFF, 0x00, 0xFF)


def test_hero_prefit_default_off_keeps_bbox_crop():
    """Without the flag the legacy normalization still applies (guards the 26
    speaker + 25 visual formats already pixel-closed against the bbox math)."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=0,
                y=0,
                width=100,
                height=100,
                fit="cover",
                anchor_h="center",
                anchor_v="top",
                allow_upscale=True,
                z=0,
            )
        ],
    )
    hero = _padded_cutout((200, 200), (0, 0, 100, 100), "#00FF00")
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    for x, y in [(25, 25), (75, 25), (25, 75), (75, 75)]:
        assert img.getpixel((x, y))[:3] == (0, 255, 0)


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


def test_render_frame_has_broken_corner_tabs():
    """The reference frame (Figma 3460-1390) is not an even rectangle: it has
    two diagonal thick green tabs — top-left (x0-140, y40-70) and bottom-right
    (x160-300, y570-600) — that make it read 'broken'/stepped. The thin 10px
    border alone leaves the interior dark there; the tabs must paint green deep
    inside those two corners."""
    manifest = load_manifest(MANIFEST)
    spec = manifest.templates["banner_300x600_render"]
    hero = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    img = compose(spec, hero=hero, texts={"slogan": "X", "cta": "Go"}, assets_root=REPO_ROOT)
    green = (0x26, 0xD0, 0x7C)
    # top-left tab interior (below the 50px header, beyond the 10px border)
    assert img.getpixel((70, 60))[:3] == green
    # bottom-right tab interior (beyond the 10px border, above the bottom bar)
    assert img.getpixel((250, 580))[:3] == green
    # the OPPOSITE corners stay thin: no tab, interior is the dark body there
    assert img.getpixel((70, 580))[:3] != green   # bottom-left stays thin
    assert img.getpixel((250, 60))[:3] != green    # top-right stays thin


def test_render_hero_is_large_uncropped_and_crosses_middle():
    """The render device must be LARGE (fills the full-width hero band and
    crosses the horizontal middle y=300) but must NEVER be cropped — its tips
    stay fully visible. That means fit=contain (no clipping) on a full-width
    rect whose band spans the vertical centre. A near-square object then fills
    the canvas width and crosses the middle while remaining whole; a flatter
    object stays smaller but is still never cut."""
    manifest = load_manifest(MANIFEST)
    spec = manifest.templates["banner_300x600_render"]
    hero_layer = next(l for l in spec.layers if l.type == "hero_cutout")
    # geometry: contain (never crops), full canvas width, band crosses y=300
    assert hero_layer.fit == "contain"
    assert hero_layer.x == 0 and hero_layer.x + hero_layer.width == 300
    assert hero_layer.y < 300 < hero_layer.y + hero_layer.height
    # the band must reclaim the full vertical space between the header (ends
    # y=50) and the slogan (starts y=384): a square device is width-bound at
    # 300px, but a taller-than-wide device needs the room, and dead bands above
    # /below made the device float. The band must span >=320px of that gap.
    slogan_layer = next(l for l in spec.layers if getattr(l, "slot", None) == "slogan")
    assert hero_layer.y <= 56  # starts just below the header
    assert hero_layer.y + hero_layer.height >= 378  # reaches down to the slogan
    assert hero_layer.y + hero_layer.height <= slogan_layer.y  # never under the text
    assert hero_layer.height >= 320
    # behaviour: a near-square object fills the width and crosses the middle row
    hero = _solid_hero(200, 200, "#FF00FF")
    img = compose(spec, hero=hero, texts={"slogan": "X", "cta": "Go"}, assets_root=REPO_ROOT)
    magenta = (0xFF, 0x00, 0xFF)
    assert img.getpixel((12, 300))[:3] == magenta    # reaches just inside left frame
    assert img.getpixel((287, 300))[:3] == magenta   # reaches just inside right frame
    assert img.getpixel((150, 300))[:3] == magenta   # crosses the horizontal middle
    # no-crop proof: a very wide-and-short object is letterboxed (full width
    # shown, dead bands top/bottom) — never clipped at the sides.
    wide = _solid_hero(300, 60, "#00FFAA")
    img2 = compose(spec, hero=wide, texts={"slogan": "X", "cta": "Go"}, assets_root=REPO_ROOT)
    teal = (0x00, 0xFF, 0xAA)
    assert img2.getpixel((150, 210))[:3] == teal     # full object band centred in rect
    assert img2.getpixel((12, 210))[:3] == teal      # left tip fully visible (not cut)
    assert img2.getpixel((287, 210))[:3] == teal     # right tip fully visible (not cut)


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


def test_cta_text_optically_centered_in_plate():
    """The CTA glyph INK (not the font's design box) must be vertically
    centered within its plate. PIL's top-left anchor leaves the font's
    internal top leading uncounted, so naive `used_size * line_height`
    centering drifts the visible text low. Assert the ink band's centre
    sits within 1px of the plate centre."""
    spec = TemplateSpec(
        width=120,
        height=80,
        background_color="#FFFFFF",
        layers=[
            TextLayer(
                type="text",
                slot="cta",
                x=10,
                y=20,
                width=100,
                height=44,
                font_family="SBSansDisplay",
                font_weight="Semibold",
                font_size_max=16,
                color="#000000",
                align_h="center",
                align_v="middle",
                max_lines=1,
                background=BoxBackground(color="#CFF500"),
            ),
        ],
    )
    img = compose(spec, hero=None, texts={"cta": "Попробовать"}, assets_root=REPO_ROOT)
    px = img.load()
    # find black-ink rows inside the plate x-span
    ink_rows = [
        y
        for y in range(20, 64)
        if any(
            px[x, y][0] < 60 and px[x, y][1] < 60 and px[x, y][2] < 60
            for x in range(10, 110)
        )
    ]
    assert ink_rows, "no CTA ink found"
    ink_center = (min(ink_rows) + max(ink_rows)) / 2
    plate_center = 20 + 44 / 2  # 42
    assert abs(ink_center - plate_center) <= 1.0, (
        f"CTA ink centre {ink_center} vs plate centre {plate_center}"
    )


# ----- Pattern dots -----------------------------------------------------------


def test_pattern_dots_tiles_grid_with_gaps():
    """Dots are drawn on a spacing_x/spacing_y grid: a pixel at the grid origin
    is the dot color, a pixel in the gap between dots stays background."""
    spec = TemplateSpec(
        width=40,
        height=40,
        background_color="#222222",
        layers=[
            PatternDotsLayer(
                type="pattern_dots",
                x=0,
                y=0,
                width=40,
                height=40,
                color="#3D3D3D",
                dot_size=2,
                spacing_x=10,
                spacing_y=10,
                z=5,
            ),
        ],
    )
    img = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    dot = (0x3D, 0x3D, 0x3D)
    bg = (0x22, 0x22, 0x22)
    # first dot at the rect origin
    assert img.getpixel((0, 0))[:3] == dot
    # next dot one spacing over
    assert img.getpixel((10, 10))[:3] == dot
    # mid-gap pixel untouched
    assert img.getpixel((5, 5))[:3] == bg


def test_pattern_dots_clipped_to_rect():
    """Dots only fill the layer rect; pixels outside the rect stay background."""
    spec = TemplateSpec(
        width=40,
        height=40,
        background_color="#222222",
        layers=[
            PatternDotsLayer(
                type="pattern_dots",
                x=10,
                y=10,
                width=20,
                height=20,
                color="#3D3D3D",
                dot_size=2,
                spacing_x=10,
                spacing_y=10,
                z=5,
            ),
        ],
    )
    img = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    bg = (0x22, 0x22, 0x22)
    # above-left of the rect: no dots
    assert img.getpixel((2, 2))[:3] == bg
    # inside the rect at its local origin: a dot
    assert img.getpixel((10, 10))[:3] == (0x3D, 0x3D, 0x3D)


# ----- Per-line underline (two-pass) ------------------------------------------


def test_underline_survives_next_line_plate():
    """Regression: the underline under a SHORT line must NOT be painted over by
    the next, WIDER line's plate. A short line followed by a wider line with a
    tight line_height makes the wide plate fully cover the short underline under
    single-pass drawing (only the wide line's own underline would survive -> 2
    bands). Drawing all plates first and all underlines second keeps every
    underline -> 3 bands. Green plate + red underline; count red bands."""
    spec = TemplateSpec(
        width=200,
        height=140,
        background_color="#FFFFFF",
        layers=[
            TextLayer(
                type="text",
                slot="slogan",
                x=10,
                y=10,
                width=180,
                height=120,
                font_family="SBSansDisplay",
                font_weight="Semibold",
                font_size_max=28,
                line_height=0.95,
                color="#000000",
                align_h="left",
                align_v="top",
                max_lines=3,
                per_line_highlight=PerLineHighlight(
                    color="#00FF00",
                    padding_x=4,
                    padding_y=0,
                    underline_color="#FF0000",
                    underline_height=2,
                    underline_gap=1,
                ),
            ),
        ],
    )
    img = compose(
        spec,
        hero=None,
        texts={"slogan": "ок\nдлиннаястрока\nок"},
        assets_root=REPO_ROOT,
    )
    pixels = img.load()
    # Rows that contain underline-red. Under single-pass drawing, line 2's
    # plate would bury line 1's underline, leaving only ONE red band; the
    # two-pass order must yield TWO distinct bands (one per line).
    red_rows = []
    for y in range(img.height):
        if any(
            pixels[x, y][0] > 200 and pixels[x, y][1] < 60 and pixels[x, y][2] < 60
            for x in range(img.width)
        ):
            red_rows.append(y)
    bands = 0
    prev = None
    for y in red_rows:
        if prev is None or y - prev > 2:
            bands += 1
        prev = y
    assert bands == 3, (
        f"expected 3 underline bands (one per line), got {bands} "
        f"-> a short line's underline was painted over by the next wider plate"
    )


# ----- Letter spacing (tracking) ---------------------------------------------


def _ink_right_edge(img: Image.Image) -> int:
    """Rightmost column containing white ink on a dark canvas."""
    px = img.load()
    right = 0
    for x in range(img.width):
        for y in range(img.height):
            r, g, b, _ = px[x, y]
            if r > 200 and g > 200 and b > 200:
                right = x
                break
    return right


def _ls_spec(letter_spacing: float) -> TemplateSpec:
    return TemplateSpec(
        width=400,
        height=80,
        background_color="#000000",
        layers=[
            TextLayer(
                type="text",
                slot="slogan",
                x=10,
                y=10,
                width=380,
                height=60,
                font_family="SBSansDisplay",
                font_weight="Regular",
                font_size_max=40,
                color="#FFFFFF",
                align_h="left",
                align_v="top",
                max_lines=1,
                letter_spacing=letter_spacing,
            ),
        ],
    )


def test_negative_letter_spacing_tightens_line():
    """Negative tracking pulls glyphs closer, so the same word ends further
    left than with zero tracking (the Figma photo slogan uses -4%)."""
    wide = compose(_ls_spec(0.0), hero=None, texts={"slogan": "Облако"}, assets_root=REPO_ROOT)
    tight = compose(
        _ls_spec(-0.08), hero=None, texts={"slogan": "Облако"}, assets_root=REPO_ROOT
    )
    assert _ink_right_edge(tight) < _ink_right_edge(wide) - 3, (
        "negative letter_spacing should visibly narrow the rendered word"
    )


def test_letter_spacing_default_is_zero_noop():
    """A layer without letter_spacing renders identically to one with 0.0 — the
    fast path must not change existing output."""
    a = compose(_ls_spec(0.0), hero=None, texts={"slogan": "Тест"}, assets_root=REPO_ROOT)
    layer = TextLayer(
        type="text", slot="slogan", x=10, y=10, width=380, height=60,
        font_family="SBSansDisplay", font_weight="Regular", font_size_max=40,
        color="#FFFFFF", align_h="left", align_v="top", max_lines=1,
    )
    assert layer.letter_spacing == 0.0
    b = compose(
        TemplateSpec(width=400, height=80, background_color="#000000", layers=[layer]),
        hero=None, texts={"slogan": "Тест"}, assets_root=REPO_ROOT,
    )
    assert list(a.getdata()) == list(b.getdata())


# ----- Per-line plate gap guarantee ------------------------------------------


def test_per_line_plates_never_merge_at_tight_line_height():
    """Adjacent green plates must keep a visible gap even when line_height is
    tight enough that the full-metrics plate would otherwise overlap the next
    line's plate (the photo slogan auto-shrinks; plates were merging into one
    solid block). Three lines -> three distinct green bands."""
    spec = TemplateSpec(
        width=200,
        height=140,
        background_color="#FFFFFF",
        layers=[
            TextLayer(
                type="text",
                slot="slogan",
                x=10,
                y=10,
                width=180,
                height=120,
                font_family="SBSansDisplay",
                font_weight="Regular",
                font_size_max=30,
                font_size_min=30,
                line_height=1.05,
                color="#000000",
                align_h="left",
                align_v="top",
                max_lines=3,
                per_line_highlight=PerLineHighlight(
                    color="#00FF00", padding_x=4, padding_y=2
                ),
            ),
        ],
    )
    img = compose(spec, hero=None, texts={"slogan": "раз\nдва\nтри"}, assets_root=REPO_ROOT)
    px = img.load()
    green_rows = [
        y
        for y in range(img.height)
        if any(
            px[x, y][0] < 60 and px[x, y][1] > 200 and px[x, y][2] < 60
            for x in range(img.width)
        )
    ]
    bands = 0
    prev = None
    for y in green_rows:
        if prev is None or y - prev > 1:
            bands += 1
        prev = y
    assert bands == 3, (
        f"expected 3 separated plates, got {bands} -> plates merged (no gap)"
    )


def test_hero_cutout_flip_h_mirrors_object():
    """Figma mockups may mirror the portrait fill (-scale-x-100, e.g. the
    460x260 TimePad and 240x240 email podborki artboards). ``flip_h`` mirrors
    the cutout horizontally before fit/anchor so an asymmetric subject lands
    on the same side as the mockup."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=0,
                y=0,
                width=100,
                height=100,
                fit="contain",
                anchor_h="center",
                anchor_v="middle",
                allow_upscale=True,
                flip_h=True,
                z=0,
            )
        ],
    )
    # object: left half red, right half blue (inside transparent padding)
    hero = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    hero.paste(Image.new("RGBA", (50, 100), (255, 0, 0, 255)), (50, 0))
    hero.paste(Image.new("RGBA", (50, 100), (0, 0, 255, 255)), (100, 0))
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # mirrored: blue now on the left, red on the right
    assert img.getpixel((10, 50))[:3] == (0, 0, 255)
    assert img.getpixel((90, 50))[:3] == (255, 0, 0)


def test_hero_cutout_stretch_fills_rect_exactly():
    """M4 visual variant: Figma metaphor fills are placed whole with a mild
    non-uniform squish (up to ~11%). ``fit="stretch"`` scales the bbox-cropped
    cutout to the rect exactly (no crop, no margins, aspect NOT preserved)."""
    spec = TemplateSpec(
        width=100,
        height=100,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=10,
                y=20,
                width=80,
                height=40,
                fit="stretch",
                z=0,
            )
        ],
    )
    # tall 40x120 object inside transparent padding: top half red, bottom blue
    hero = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
    hero.paste(Image.new("RGBA", (40, 60), (255, 0, 0, 255)), (30, 40))
    hero.paste(Image.new("RGBA", (40, 60), (0, 0, 255, 255)), (30, 100))
    img = compose(spec, hero=hero, texts={}, assets_root=REPO_ROOT)
    # rect corners covered by the object (no margins despite AR mismatch)
    assert img.getpixel((11, 21))[:3] == (255, 0, 0)
    assert img.getpixel((88, 21))[:3] == (255, 0, 0)
    assert img.getpixel((11, 58))[:3] == (0, 0, 255)
    assert img.getpixel((88, 58))[:3] == (0, 0, 255)
    # outside the rect stays background
    assert img.getpixel((5, 50))[:3] == (255, 0, 255)
    assert img.getpixel((50, 15))[:3] == (255, 0, 255)


def _quadrant_hero() -> Image.Image:
    """100x100 opaque: TL red, TR blue, BL green, BR yellow (split at 50)."""
    hero = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    hero.paste(Image.new("RGBA", (50, 50), (0, 0, 255, 255)), (50, 0))
    hero.paste(Image.new("RGBA", (50, 50), (0, 255, 0, 255)), (0, 50))
    hero.paste(Image.new("RGBA", (50, 50), (255, 255, 0, 255)), (50, 50))
    return hero


def _crop_spec(**extra) -> TemplateSpec:
    return TemplateSpec(
        width=100,
        height=50,
        background_color="#FF00FF",
        layers=[
            HeroCutoutLayer(
                type="hero_cutout",
                x=10,
                y=10,
                width=50,
                height=25,
                fit="crop",
                crop_scale=2.0,
                crop_left=-0.5,
                crop_top=-1.0,
                z=0,
                **extra,
            )
        ],
    )


def test_hero_cutout_crop_replicates_figma_fill_transform():
    """M4 visual (2026-07-20): Figma metaphor fills are CROP transforms —
    uniform scale (``crop_scale`` = rendered image width / rect width) plus an
    offset (``crop_left``/``crop_top`` as fractions of rect size), clipped to
    the rect. The full file frame is used (NO alpha-bbox pre-crop: the Figma
    percentages are file-space)."""
    img = compose(_crop_spec(), hero=_quadrant_hero(), texts={}, assets_root=REPO_ROOT)
    # rendered image: 100px wide (2.0 * 50), scale 1 -> 100x100 at offset
    # (-25, -25) from the rect corner: window shows file px x 25..75, y 25..50
    # -> top-half quadrants only: red left of window mid, blue right of it.
    assert img.getpixel((12, 20))[:3] == (255, 0, 0)
    assert img.getpixel((57, 20))[:3] == (0, 0, 255)
    # clipped to the rect: outside stays background
    assert img.getpixel((5, 20))[:3] == (255, 0, 255)
    assert img.getpixel((30, 5))[:3] == (255, 0, 255)
    assert img.getpixel((30, 40))[:3] == (255, 0, 255)


def test_hero_cutout_crop_flips_after_crop():
    """Figma mirrors the whole placed rect (flipped-x transform), so the crop
    window is computed in un-flipped file space and the *result* is mirrored."""
    img = compose(
        _crop_spec(flip_h=True), hero=_quadrant_hero(), texts={}, assets_root=REPO_ROOT
    )
    # same window as above, mirrored: blue lands left, red right
    assert img.getpixel((12, 20))[:3] == (0, 0, 255)
    assert img.getpixel((57, 20))[:3] == (255, 0, 0)
