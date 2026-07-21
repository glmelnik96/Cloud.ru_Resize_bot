"""Webinar manifest (M4-web, 2026-07-15): 26 ready-made resizes.

First format under test: webinar_600x600_speaker. Contract:
  1. The manifest loads and the scenario references a real format.
  2. Composing with an alpha-PNG speaker + title/subtitle produces a 600x600
     RGBA banner with the brand furniture in place (green panel, lime button
     plate, dark background) and the baked labels rendered.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from infra.composer import compose
from infra.template_manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest(REPO_ROOT / "config" / "webinar_templates.json")


def _speaker(color=(0, 0, 0, 255)) -> Image.Image:
    # opaque portrait-ish cutout on a transparent margin
    im = Image.new("RGBA", (300, 450), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (200, 400), color), (50, 40))
    return im


def test_scenario_references_real_format():
    sc = MANIFEST.scenarios["webinar_speaker"]
    # Form slots: name + position are collected separately and folded into the
    # single 2-line `subtitle` text box by the service (speaker_subtitle).
    assert sc.slots == ["title", "name", "position", "date", "time"]
    for fmt in sc.formats:
        assert fmt in MANIFEST.templates


def test_compose_600_speaker_has_brand_furniture():
    spec = MANIFEST.templates["webinar_600x600_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={"title": "Заголовок вебинара про облако", "subtitle": "Имя Фамилия,\nдолжность"},
        assets_root=REPO_ROOT,
        slug="webinar_600x600_speaker",
    )
    assert img.size == (600, 600)
    # dark background at a top-center gap (above the green panel, left of badge)
    assert img.getpixel((300, 12))[:3] == (0x22, 0x22, 0x22)
    # green panel on the left-center
    assert img.getpixel((40, 300))[:3] == (0x26, 0xD0, 0x7C)
    # lime button plate near the bottom (probe off-center so a glyph can't sit under it)
    assert img.getpixel((60, 530))[:3] == (0xCF, 0xF5, 0x00)


def test_compose_600_speaker_only_brand_excludes_green_panel():
    """The green panel is brand furniture in the ``hero`` group, so a brand-only
    artifact leaves the panel area transparent."""
    spec = MANIFEST.templates["webinar_600x600_speaker"]
    img = compose(
        spec,
        hero=None,
        texts={"title": "T", "subtitle": "S"},
        assets_root=REPO_ROOT,
        only={"brand"},
    )
    assert img.getpixel((40, 300))[3] == 0


def test_compose_600_date_variant_has_date_row():
    spec = MANIFEST.templates["webinar_600x600_date_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Заголовок вебинара про облако",
            "subtitle": "Имя Фамилия,\nдолжность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_600x600_date_speaker",
    )
    assert img.size == (600, 600)
    # green panel present, but shorter than the speaker variant (ends ~y415, so the
    # date row below it sits on the dark background)
    assert img.getpixel((40, 300))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((180, 475))[:3] == (0x22, 0x22, 0x22)
    # white date glyphs present on the left of the row
    row = img.crop((30, 438, 205, 466))
    assert any(
        row.getpixel((x, y))[:3] == (0xFF, 0xFF, 0xFF)
        for x in range(0, row.width, 2)
        for y in range(0, row.height, 2)
    )


def test_compose_600_portrait_variant_has_dual_badges():
    spec = MANIFEST.templates["webinar_600x600_portrait_speaker"]
    # The speaker is a full-bleed cover fill clipped to the green body (Figma
    # node 11749: w550 cover), so a solid cutout would hide the panel; pass a
    # fully transparent hero (a documented no-op cutout) to assert the brand
    # geometry is in place.
    img = compose(
        spec,
        hero=Image.new("RGBA", (300, 450), (0, 0, 0, 0)),
        texts={"title": "T", "subtitle": "S"},
        assets_root=REPO_ROOT,
        slug="webinar_600x600_portrait_speaker",
    )
    assert img.size == (600, 600)
    # taller green panel (reaches down near the bottom)
    assert img.getpixel((40, 500))[:3] == (0x26, 0xD0, 0x7C)
    # blue "Вебинар" badge pill (probe the left padding, clear of the dark glyphs)
    assert img.getpixel((318, 50))[:3] == (0xC4, 0xDA, 0xF7)
    # green "Бесплатно" badge pill
    assert img.getpixel((450, 50))[:3] == (0x26, 0xD0, 0x7C)


def _has(img, rgb) -> bool:
    return any(
        img.getpixel((x, y))[:3] == rgb
        for x in range(0, img.width, 3)
        for y in range(0, img.height, 3)
    )


def test_compose_1080x1350_has_texture_and_dual_badges():
    spec = MANIFEST.templates["webinar_1080x1350_speaker"]
    # The speaker is a full-bleed cover fill clipped to the green body, so a real
    # cutout would hide most of the panel; pass a fully transparent hero (a
    # documented no-op cutout) to assert the brand geometry is in place.
    img = compose(
        spec,
        hero=Image.new("RGBA", (300, 450), (0, 0, 0, 0)),
        texts={"title": "T", "subtitle": "S"},
        assets_root=REPO_ROOT,
        slug="webinar_1080x1350_speaker",
    )
    assert img.size == (1080, 1350)
    # green panel (inset 50px per Figma border) on the left, clear of the speaker
    assert img.getpixel((70, 700))[:3] == (0x26, 0xD0, 0x7C)
    # dark 50px margin outside the green body
    assert img.getpixel((20, 700))[:3] == (0x22, 0x22, 0x22)
    # tiled arrow texture (darker green #159A57) in the right strip
    assert _has(img.crop((640, 300, 940, 1180)), (0x15, 0x9A, 0x57))
    # dual badges top-right: blue "Вебинар" + light-mint "Бесплатно"
    assert _has(img.crop((639, 44, 820, 114)), (0xC4, 0xDA, 0xF7))
    assert _has(img.crop((822, 44, 1030, 114)), (0x8E, 0xE7, 0xBB))


def test_compose_1280x720_youtube_has_brand_bar_and_body():
    spec = MANIFEST.templates["webinar_1280x720_youtube_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1280x720_youtube_speaker",
    )
    assert img.size == (1280, 720)
    # green body (inset 30px per Figma border) on the left, clear of the speaker
    assert img.getpixel((70, 400))[:3] == (0x26, 0xD0, 0x7C)
    # dark strip above the body (top brand bar sits on the dark background)
    assert img.getpixel((640, 10))[:3] == (0x22, 0x22, 0x22)
    # grey brand-bar ticks up top
    assert _has(img.crop((289, 21, 1062, 69)), (0x64, 0x64, 0x64))
    # black title glyphs on the green body
    assert _has(img.crop((85, 219, 753, 479)), (0x22, 0x22, 0x22))


def test_compose_1920x1080_vc_has_badge_body_and_date_row():
    spec = MANIFEST.templates["webinar_1920x1080_vc_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1920x1080_vc_speaker",
    )
    assert img.size == (1920, 1080)
    # green body (inset 56px per Figma border-56) on the left, clear of the speaker
    assert img.getpixel((300, 500))[:3] == (0x26, 0xD0, 0x7C)
    # dark 56px margin outside the green body
    assert img.getpixel((20, 500))[:3] == (0x22, 0x22, 0x22)
    # bottom-left dark notch under the date row
    assert img.getpixel((40, 1000))[:3] == (0x22, 0x22, 0x22)
    # badge outline: black stroke box on the green, transparent interior
    assert _has(img.crop((111, 124, 351, 205)), (0x22, 0x22, 0x22))
    # white date glyphs on the notch (left of the arrow)
    assert _has(img.crop((60, 950, 420, 1040)), (0xFF, 0xFF, 0xFF))
    # green long arrow between date and time
    assert _has(img.crop((441, 950, 726, 1020)), (0x26, 0xD0, 0x7C))


def test_compose_1920x1080_advert_has_brand_bar_mint_badge_and_body():
    spec = MANIFEST.templates["webinar_1920x1080_advert_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1920x1080_advert_speaker",
    )
    assert img.size == (1920, 1080)
    # green body pushed down (Figma body top-180, border-56 -> green starts y236)
    assert img.getpixel((300, 240))[:3] == (0x26, 0xD0, 0x7C)
    # dark brand-bar strip above the green body
    assert img.getpixel((300, 228))[:3] == (0x22, 0x22, 0x22)
    # colored cloud.ru logo (green icon) top-left on the black bar
    assert _has(img.crop((56, 45, 487, 124)), (0x26, 0xD0, 0x7C))
    # grey tick ruler in the brand bar
    assert _has(img.crop((544, 40, 1510, 130)), (0x64, 0x64, 0x64))
    # filled light-mint "Вебинары" badge
    assert img.getpixel((120, 318))[:3] == (0x8E, 0xE7, 0xBB)
    # bottom-left notch under the date row
    assert img.getpixel((40, 1000))[:3] == (0x22, 0x22, 0x22)
    # green long arrow in the date row
    assert _has(img.crop((441, 950, 670, 1020)), (0x26, 0xD0, 0x7C))


def test_compose_1920x1080_advert2_has_outline_badge_and_bottom_disclaimer():
    spec = MANIFEST.templates["webinar_1920x1080_advert2_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1920x1080_advert2_speaker",
    )
    assert img.size == (1920, 1080)
    # green body (Figma body top-180, border-56 -> green starts y236)
    assert img.getpixel((300, 240))[:3] == (0x26, 0xD0, 0x7C)
    # smaller top-right notch (h140): black at y200, green again by y330
    assert img.getpixel((1500, 200))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((1000, 330))[:3] == (0x26, 0xD0, 0x7C)
    # outline "Вебинар" badge: black stroke on the green, no mint fill
    assert _has(img.crop((111, 295, 351, 376)), (0x22, 0x22, 0x22))
    assert not _has(img.crop((111, 295, 351, 376)), (0x8E, 0xE7, 0xBB))
    # bottom-left notch carries the disclaimer (no date row on this variant)
    assert img.getpixel((40, 1000))[:3] == (0x22, 0x22, 0x22)


def test_compose_2560x1440_youtube_cover_has_brand_bar_body_and_disclaimer():
    spec = MANIFEST.templates["webinar_2560x1440_youtube_cover_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
        },
        assets_root=REPO_ROOT,
        slug="webinar_2560x1440_youtube_cover_speaker",
    )
    assert img.size == (2560, 1440)
    # green body (Figma body top border-56 -> green starts y226)
    assert img.getpixel((300, 230))[:3] == (0x26, 0xD0, 0x7C)
    # top-right notch black; bottom-left notch black (carries disclaimer)
    assert img.getpixel((1400, 200))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((300, 1350))[:3] == (0x22, 0x22, 0x22)
    # no date row / no badge on this variant
    assert not _has(img.crop((111, 295, 351, 376)), (0x8E, 0xE7, 0xBB))


def test_compose_1900x500_has_left_column_body_and_yellow_button():
    spec = MANIFEST.templates["webinar_1900x500_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1900x500_speaker",
    )
    assert img.size == (1900, 500)
    # green body content box (border-50 -> starts x670 y50)
    assert img.getpixel((900, 60))[:3] == (0x26, 0xD0, 0x7C)
    # left column stays black (left of the speaker box, below the logo)
    assert img.getpixel((20, 200))[:3] == (0x22, 0x22, 0x22)
    # top-right + bottom-left notches carve the green
    assert img.getpixel((1700, 40))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((920, 470))[:3] == (0x22, 0x22, 0x22)
    # yellow register button in the green body
    assert img.getpixel((1500, 370))[:3] == (0xCF, 0xF5, 0x00)
    # white-outline "Вебинар" badge in the black column
    assert _has(img.crop((50, 369, 445, 450)), (0xFF, 0xFF, 0xFF))


def test_compose_1144x267_has_left_column_body_and_yellow_button():
    spec = MANIFEST.templates["webinar_1144x267_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия, должность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1144x267_speaker",
    )
    assert img.size == (1144, 267)
    # green body content box (border-20 -> starts x442 y21)
    assert img.getpixel((750, 40))[:3] == (0x26, 0xD0, 0x7C)
    # left column stays black (left of speaker box, below logo)
    assert img.getpixel((10, 110))[:3] == (0x22, 0x22, 0x22)
    # top-right notch carves the green
    assert img.getpixel((1050, 20))[:3] == (0x22, 0x22, 0x22)
    # yellow register button in the green body
    assert img.getpixel((820, 208))[:3] == (0xCF, 0xF5, 0x00)
    # white-outline "Вебинар" badge in the black column
    assert _has(img.crop((30, 166, 290, 219)), (0xFF, 0xFF, 0xFF))


def test_compose_640x360_has_full_width_body_blue_badge_and_button():
    spec = MANIFEST.templates["webinar_640x360_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
        },
        assets_root=REPO_ROOT,
        slug="webinar_640x360_speaker",
    )
    assert img.size == (640, 360)
    # full-width green body (border-10 -> green from y80), sampled right of speaker
    assert img.getpixel((610, 320))[:3] == (0x26, 0xD0, 0x7C)
    # top bar stays black (between logo and badge)
    assert img.getpixel((300, 30))[:3] == (0x22, 0x22, 0x22)
    # blue "Вебинар" badge top-right
    assert img.getpixel((560, 30))[:3] == (0xC4, 0xDA, 0xF7)
    # top-right notch carves the green
    assert img.getpixel((600, 80))[:3] == (0x22, 0x22, 0x22)
    # yellow register button
    assert img.getpixel((430, 281))[:3] == (0xCF, 0xF5, 0x00)


def test_compose_632x396_ivent_has_body_dots_grid_and_crest():
    spec = MANIFEST.templates["webinar_632x396_ivent_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={},
        assets_root=REPO_ROOT,
        slug="webinar_632x396_ivent_speaker",
    )
    assert img.size == (632, 396)
    # green body (border-30 -> starts x30 y30), sampled right of speaker
    assert img.getpixel((560, 80))[:3] == (0x26, 0xD0, 0x7C)
    # top-left + bottom-right notches carve the green
    assert img.getpixel((50, 30))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((600, 370))[:3] == (0x22, 0x22, 0x22)
    # black dots grid on the green (top-left of the grid)
    assert img.getpixel((54, 221))[:3] == (0x22, 0x22, 0x22)
    # crest "+" mark top-right: its center arm renders near-black on the green
    cx, cy = 554 + 16, 43 + 16
    assert max(img.getpixel((cx, cy))[:3]) < 60
    # no title/badge/button on this minimal variant
    assert "title" not in {layer.name for layer in spec.layers}


def test_compose_1080x607_vk_has_body_dual_badges_texture_and_dots():
    spec = MANIFEST.templates["webinar_1080x607_vk_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={},
        assets_root=REPO_ROOT,
        slug="webinar_1080x607_vk_speaker",
    )
    assert img.size == (1080, 607)
    # green body (border-30 -> green from x30 y120), sampled right of speaker
    assert img.getpixel((950, 300))[:3] == (0x26, 0xD0, 0x7C)
    # top-left + bottom-right notches carve the green
    assert img.getpixel((40, 150))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((1040, 500))[:3] == (0x22, 0x22, 0x22)
    # dual badges: blue "Вебинар" + mint "Бесплатно"
    assert img.getpixel((795, 34))[:3] == (0xC4, 0xDA, 0xF7)
    assert img.getpixel((918, 34))[:3] == (0x8E, 0xE7, 0xBB)
    # white dots grid top-right corner dot
    assert img.getpixel((898, 142))[:3] == (0xFF, 0xFF, 0xFF)
    # arrow texture region carries near-black strokes on the green
    assert _has(img.crop((132, 190, 521, 511)), (0x22, 0x22, 0x22))
    # no title/button on this ad variant
    assert "title" not in {layer.name for layer in spec.layers}


def test_compose_1080x1080_tgk_has_date_row_title_and_button():
    spec = MANIFEST.templates["webinar_1080x1080_tgk"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Продукт: описание",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1080x1080_tgk",
    )
    assert img.size == (1080, 1080)
    # inline date row up top carries green glyphs (arrows + green date/time)
    assert _has(img.crop((40, 74, 1040, 116)), (0x26, 0xD0, 0x7C))
    # two-tone title below: white product name + green tagline
    title = img.crop((41, 191, 1040, 361))
    assert _has(title, (0xFF, 0xFF, 0xFF))
    assert _has(title, (0x26, 0xD0, 0x7C))
    # green panel bottom half (inset 40px per Figma border, so probe clear of the edge)
    assert img.getpixel((70, 700))[:3] == (0x26, 0xD0, 0x7C)
    # green "Tag" button plate bottom-right (Figma #26D07C, not lime)
    assert img.getpixel((700, 1020))[:3] == (0x26, 0xD0, 0x7C)


def test_compose_1080x1920_story_has_body_header_rows_and_dot_grids():
    spec = MANIFEST.templates["webinar_1080x1920_story_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса в облаке",
            "subtitle": "Имя Фамилия,\nдолжность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1080x1920_story_speaker",
    )
    assert img.size == (1080, 1920)
    # green body (border-60 -> green from x60 y210), sampled top-left clear of text
    assert img.getpixel((70, 220))[:3] == (0x26, 0xD0, 0x7C)
    # top-right + bottom-left notches carve the green
    assert img.getpixel((1000, 300))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((100, 1500))[:3] == (0x22, 0x22, 0x22)
    # two header rows carry the dark diagonal arrow glyphs + black date/time text
    assert _has(img.crop((115, 271, 1020, 321)), (0x22, 0x22, 0x22))
    # bottom-left wide dot grid sits in front of the speaker (z4)
    assert img.getpixel((211, 1567))[:3] == (0xFF, 0xFF, 0xFF)
    # top-right tight dot grid sits behind the speaker (z2): reveal it with a
    # fully transparent hero so the opaque synthetic speaker can't mask it.
    ghost = compose(
        spec,
        hero=Image.new("RGBA", (300, 450), (0, 0, 0, 0)),
        texts={"title": "T", "subtitle": "S", "date": "30 июля", "time": "11:00"},
        assets_root=REPO_ROOT,
        slug="webinar_1080x1920_story_speaker",
    )
    assert ghost.getpixel((836, 687))[:3] == (0xFF, 0xFF, 0xFF)


def test_compose_1080x1920_story2_moves_disclaimer_to_top():
    """Variant 2 is identical to story 1 but the faint legal line sits in the
    top dark margin (above the green body) instead of at the bottom."""
    spec = MANIFEST.templates["webinar_1080x1920_story2_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={
            "title": "Evolution Managed BI: все возможности сервиса",
            "subtitle": "Имя Фамилия,\nдолжность",
            "date": "30 июля",
            "time": "11:00",
        },
        assets_root=REPO_ROOT,
        slug="webinar_1080x1920_story2_speaker",
    )
    assert img.size == (1080, 1920)
    # same green body furniture
    assert img.getpixel((70, 220))[:3] == (0x26, 0xD0, 0x7C)
    # faint legal line renders in the top dark margin (y168 band)
    band = img.crop((60, 160, 920, 200))
    assert any(
        band.getpixel((x, y))[:3] != (0x22, 0x22, 0x22)
        for x in range(0, band.width, 4)
        for y in range(0, band.height, 3)
    )
    # bottom margin is clean (no disclaimer there in this variant)
    assert img.getpixel((600, 1885))[:3] == (0x22, 0x22, 0x22)


_EMAIL_TEXTS = {
    "title": "Evolution Managed BI: все возможности BI-сервиса в облаке",
    "subtitle": "Михаил Безобразов, архитектор решений",
    "date": "30 июля",
    "time": "11:00",
}


def _email(slug):
    return compose(
        MANIFEST.templates[slug],
        hero=_speaker(),
        texts=_EMAIL_TEXTS,
        assets_root=REPO_ROOT,
        slug=slug,
    )


def test_compose_1200x600_email1_has_badge_dividers_and_date_row():
    img = _email("webinar_1200x600_email1_speaker")
    assert img.size == (1200, 600)
    # green badge (dark "Вебинар" text on it) top-left
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)
    # media panel is green (reveal behind the opaque synthetic speaker via a
    # fully transparent ghost hero)
    ghost = compose(
        MANIFEST.templates["webinar_1200x600_email1_speaker"],
        hero=Image.new("RGBA", (300, 450), (0, 0, 0, 0)),
        texts=_EMAIL_TEXTS,
        assets_root=REPO_ROOT,
        slug="webinar_1200x600_email1_speaker",
    )
    assert ghost.getpixel((900, 300))[:3] == (0x26, 0xD0, 0x7C)
    # left + right corner notches carve the media panel dark
    assert img.getpixel((610, 60))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((1180, 520))[:3] == (0x22, 0x22, 0x22)
    # both green dividers (header + footer)
    assert img.getpixel((300, 145))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((300, 466))[:3] == (0x26, 0xD0, 0x7C)
    # date/time row carries white glyphs
    assert _has(img.crop((60, 490, 540, 530)), (0xFF, 0xFF, 0xFF))


def test_compose_1200x600_email2_has_fixed_badge_and_date_row():
    img = _email("webinar_1200x600_email2_speaker")
    assert img.size == (1200, 600)
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((300, 145))[:3] == (0x26, 0xD0, 0x7C)
    # footer divider sits lower than email-1 (y513)
    assert img.getpixel((300, 514))[:3] == (0x26, 0xD0, 0x7C)
    assert _has(img.crop((60, 508, 540, 548)), (0xFF, 0xFF, 0xFF))


def test_compose_1200x600_email3_has_white_watch_button():
    img = _email("webinar_1200x600_email3_speaker")
    assert img.size == (1200, 600)
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)
    # white recording button plate at y459..540
    assert img.getpixel((70, 500))[:3] == (0xFF, 0xFF, 0xFF)
    # dark button caption glyphs
    assert _has(img.crop((60, 459, 540, 540)), (0x22, 0x22, 0x22))


def test_compose_1200x600_email4_has_top_and_bottom_dividers_only():
    img = _email("webinar_1200x600_email4_speaker")
    assert img.size == (1200, 600)
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)
    # top divider (y144) + bottom footer divider (y538), no date row between
    assert img.getpixel((300, 145))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((300, 539))[:3] == (0x26, 0xD0, 0x7C)


def test_scenario_covers_all_26_formats():
    assert len(MANIFEST.scenarios["webinar_speaker"].formats) == 26


def test_compose_460x260_timepad_has_body_texture_and_notches():
    spec = MANIFEST.templates["webinar_460x260_timepad_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={},
        assets_root=REPO_ROOT,
        slug="webinar_460x260_timepad_speaker",
    )
    assert img.size == (460, 260)
    # dark canvas above the green body (right of the logo)
    assert img.getpixel((300, 20))[:3] == (0x22, 0x22, 0x22)
    # body frame is border-16: green interior x16 y65 w428 h179, dark ring left
    assert img.getpixel((20, 97))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((7, 120))[:3] == (0x22, 0x22, 0x22)
    # top-right + bottom-left notches carve the green
    assert img.getpixel((445, 80))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((15, 220))[:3] == (0x22, 0x22, 0x22)
    # full-bleed arrow texture: dark strokes on the green strip left of the speaker
    assert _has(img.crop((16, 65, 70, 168)), (0x22, 0x22, 0x22))
    names = {layer.name for layer in spec.layers}
    assert "logo" in names
    assert "title" not in names


def test_compose_240x240_email_podborki_is_speaker_only():
    spec = MANIFEST.templates["webinar_240x240_email_podborki_speaker"]
    img = compose(
        spec,
        hero=_speaker(),
        texts={},
        assets_root=REPO_ROOT,
        slug="webinar_240x240_email_podborki_speaker",
    )
    assert img.size == (240, 240)
    # dark canvas above the speaker box (y<22)
    assert img.getpixel((5, 5))[:3] == (0x22, 0x22, 0x22)
    # the cover speaker fills the canvas below
    assert img.getpixel((120, 120))[:3] == (0, 0, 0)
    names = {layer.name for layer in spec.layers}
    assert "legal" in names
    assert "title" not in names


def _brand_bar(slug):
    spec = MANIFEST.templates[slug]
    img = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT, slug=slug)
    return spec, img


def test_compose_2560x170_brand_box_has_ruler_and_tagline():
    spec, img = _brand_bar("webinar_2560x170_brand_box")
    assert img.size == (2560, 170)
    # first ruler line at x544, gap right after is dark, band spans to x2148
    assert img.getpixel((544, 85))[:3] == (0x64, 0x64, 0x64)
    assert img.getpixel((553, 85))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((2148, 85))[:3] == (0x64, 0x64, 0x64)
    # above the band is dark
    assert img.getpixel((544, 30))[:3] == (0x22, 0x22, 0x22)
    # white tagline on the right
    assert _has(img.crop((2150, 42, 2504, 142)), (0xFF, 0xFF, 0xFF))
    assert "logo" in {layer.name for layer in spec.layers}


def test_compose_1920x170_strip_clips_ruler_before_tagline():
    spec, img = _brand_bar("webinar_1920x170_strip")
    assert img.size == (1920, 170)
    assert img.getpixel((544, 85))[:3] == (0x64, 0x64, 0x64)
    assert img.getpixel((553, 85))[:3] == (0x22, 0x22, 0x22)
    # band clips at x1509: no grey ruler past it (probe a period-aligned spot)
    assert img.getpixel((1516, 85))[:3] == (0x22, 0x22, 0x22)
    # white tagline left-anchored at x1566
    assert _has(img.crop((1566, 42, 1900, 142)), (0xFF, 0xFF, 0xFF))
    assert "logo" in {layer.name for layer in spec.layers}


# --- visual variant (metaphor boards 3552:10086 / 3552:11362) -----------------
# The metaphor is an alpha-cutout render placed WHOLE into a measured rect
# (fit="stretch") and mirrored (flip_h) like the Figma fills (-scale-x-100).


def _metaphor() -> Image.Image:
    # opaque square "render": left half red, right half blue (mirror probe)
    im = Image.new("RGBA", (400, 400), (200, 30, 30, 255))
    im.paste(Image.new("RGBA", (200, 400), (30, 30, 200, 255)), (200, 0))
    return im


def test_visual_scenario_slots_and_hero_policy():
    sc = MANIFEST.scenarios["webinar_visual"]
    assert sc.slots == ["title", "date", "time"]
    assert sc.hero.source == "generate"
    assert sc.hero.remove_bg is True
    for fmt in sc.formats:
        assert fmt in MANIFEST.templates


def test_visual_scenario_covers_all_25_formats():
    # The visual boards (3552:10086 / 3552:11362) carry 25 artboards: the
    # speaker set minus the two hero-less brand strips (2560x170 / 1920x170)
    # and the second story, plus the event-branded email-1.
    sc = MANIFEST.scenarios["webinar_visual"]
    assert len(sc.formats) == len(set(sc.formats)) == 25
    visual_templates = {k for k in MANIFEST.templates if k.endswith("_visual")}
    assert set(sc.formats) == visual_templates


def test_compose_600x600_visual_mirrors_metaphor_no_subtitle():
    spec = MANIFEST.templates["webinar_600x600_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "legal" not in names
    img = compose(
        spec,
        hero=_metaphor(),
        texts={"title": "Т"},
        assets_root=REPO_ROOT,
    )
    assert img.size == (600, 600)
    assert img.getpixel((300, 12))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((40, 300))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((60, 530))[:3] == (0xCF, 0xF5, 0x00)
    # metaphor rect x357..529 y172..425, mirrored: blue lands on the left half
    assert img.getpixel((390, 300))[:3] == (30, 30, 200)
    assert img.getpixel((500, 300))[:3] == (200, 30, 30)


def test_compose_600x600_date_visual_has_date_row():
    spec = MANIFEST.templates["webinar_600x600_date_visual"]
    img = compose(
        spec,
        hero=_metaphor(),
        texts={"title": "Т", "date": "08 сентября", "time": "11:00"},
        assets_root=REPO_ROOT,
    )
    assert img.getpixel((40, 300))[:3] == (0x26, 0xD0, 0x7C)
    # white date/time row in the y438..470 band
    assert _has(img.crop((30, 430, 570, 470)), (0xFF, 0xFF, 0xFF))
    # metaphor rect x357..529 y170..394
    assert img.getpixel((390, 280))[:3] == (30, 30, 200)
    assert img.getpixel((500, 280))[:3] == (200, 30, 30)


def test_compose_600x600_big_visual_is_textless_with_dots_and_dual_badges():
    spec = MANIFEST.templates["webinar_600x600_big_visual"]
    names = {layer.name for layer in spec.layers}
    assert "title" not in names and "button" not in names
    assert "badge2" in names
    img = compose(spec, hero=_metaphor(), texts={}, assets_root=REPO_ROOT)
    assert img.getpixel((40, 300))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((360, 100))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    # dots grid darkens the green panel left of the metaphor rect
    assert _has(img.crop((54, 150, 178, 274)), (0x22, 0x22, 0x22))
    # metaphor rect x198..502 y185..582
    assert img.getpixel((250, 380))[:3] == (30, 30, 200)
    assert img.getpixel((450, 380))[:3] == (200, 30, 30)


def test_compose_460x260_timepad_visual_no_legal():
    spec = MANIFEST.templates["webinar_460x260_timepad_visual"]
    names = {layer.name for layer in spec.layers}
    assert "legal" not in names
    img = compose(spec, hero=_metaphor(), texts={}, assets_root=REPO_ROOT)
    assert img.getpixel((20, 97))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((445, 80))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    # metaphor rect x280..433 y17..203
    assert img.getpixel((300, 100))[:3] == (30, 30, 200)
    assert img.getpixel((410, 100))[:3] == (200, 30, 30)


def test_compose_632x396_ivent_visual_no_disclaimer():
    spec = MANIFEST.templates["webinar_632x396_ivent_visual"]
    names = {layer.name for layer in spec.layers}
    assert "disclaimer" not in names
    img = compose(spec, hero=_metaphor(), texts={}, assets_root=REPO_ROOT)
    assert img.getpixel((40, 200))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((100, 30))[:3] == (0x22, 0x22, 0x22)  # notch_tl
    # metaphor rect x263..524 y55..374
    assert img.getpixel((300, 200))[:3] == (30, 30, 200)
    assert img.getpixel((500, 200))[:3] == (200, 30, 30)


# --- visual variant, family V2 (1080/1920-блок, board 3552:10086) --------------


_V2_TEXTS = {"title": "Т", "date": "08 сентября", "time": "11:00"}


def _visual(slug, texts=_V2_TEXTS):
    return compose(
        MANIFEST.templates[slug],
        hero=_metaphor(),
        texts=texts,
        assets_root=REPO_ROOT,
        slug=slug,
    )


def test_compose_1080x1080_visual_mirrors_metaphor_no_legal():
    spec = MANIFEST.templates["webinar_1080x1080_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "legal" not in names
    img = _visual("webinar_1080x1080_visual")
    assert img.size == (1080, 1080)
    # body strip left of the metaphor (metaphor starts x67, body x40)
    assert img.getpixel((50, 700))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1075, 470))[:3] == (0x22, 0x22, 0x22)  # notch_tr sliver right of metaphor
    assert img.getpixel((40, 900))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    assert img.getpixel((42, 385))[:3] == (0xFF, 0xFF, 0xFF)  # dots_tl corner dot
    # metaphor rect x67..1070 y385..1040 mirrored: blue lands left of mid 568
    assert img.getpixel((300, 700))[:3] == (30, 30, 200)
    assert img.getpixel((900, 700))[:3] == (200, 30, 30)


def test_compose_1080x1080_tgk_visual_keeps_date_row_and_plate():
    spec = MANIFEST.templates["webinar_1080x1080_tgk_visual"]
    assert "legal" not in {layer.name for layer in spec.layers}
    img = _visual("webinar_1080x1080_tgk_visual")
    assert img.size == (1080, 1080)
    # inline date row up top carries green glyphs (arrows + green date/time)
    assert _has(img.crop((40, 74, 1040, 116)), (0x26, 0xD0, 0x7C))
    # metaphor covers the whole green body -> assert the layer itself
    body = next(layer for layer in spec.layers if layer.name == "green_body")
    assert body.color == "#26D07C"
    assert img.getpixel((700, 1020))[:3] == (0x26, 0xD0, 0x7C)  # Tag plate
    # metaphor rect x37..1040 y411..960 mirrored around mid 538
    assert img.getpixel((200, 700))[:3] == (30, 30, 200)
    assert img.getpixel((800, 700))[:3] == (200, 30, 30)


def test_compose_1080x607_vk_visual_no_disclaimer():
    spec = MANIFEST.templates["webinar_1080x607_vk_visual"]
    names = {layer.name for layer in spec.layers}
    assert "disclaimer" not in names and "title" not in names
    img = _visual("webinar_1080x607_vk_visual")
    assert img.size == (1080, 607)
    assert img.getpixel((110, 550))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((795, 34))[:3] == (0xC4, 0xDA, 0xF7)  # blue badge
    assert img.getpixel((918, 34))[:3] == (0x8E, 0xE7, 0xBB)  # mint badge
    # arrow texture strokes on the green, left of the metaphor
    assert _has(img.crop((132, 190, 500, 500)), (0x22, 0x22, 0x22))
    # metaphor rect x521..1009 y120..586 mirrored around mid 765
    assert img.getpixel((600, 300))[:3] == (30, 30, 200)
    assert img.getpixel((950, 300))[:3] == (200, 30, 30)


def test_compose_1080x1350_visual_is_textless_with_texture():
    spec = MANIFEST.templates["webinar_1080x1350_visual"]
    names = {layer.name for layer in spec.layers}
    assert "title" not in names and "subtitle" not in names and "legal" not in names
    img = _visual("webinar_1080x1350_visual")
    assert img.size == (1080, 1350)
    # green body2 panel (x99..1079; x<99 is dark page bg after the canon rebuild)
    assert img.getpixel((600, 200))[:3] == (0x26, 0xD0, 0x7C)
    assert img.getpixel((700, 160))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    assert img.getpixel((200, 1240))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    # dark dots grid on the green (top-left)
    assert img.getpixel((96, 242))[:3] == (0x22, 0x22, 0x22)
    # arrow texture band lives under the metaphor rect (hidden by the opaque
    # test hero, visible through the real cutout's alpha) — assert the layer
    assert any(
        layer.name == "arrow_texture" and layer.type == "texture"
        for layer in spec.layers
    )
    # dual badges
    assert _has(img.crop((639, 44, 820, 115)), (0xC4, 0xDA, 0xF7))
    assert _has(img.crop((822, 44, 1030, 115)), (0x8E, 0xE7, 0xBB))
    # metaphor rect x88..1048 y270..1230 mirrored around mid 568
    assert img.getpixel((300, 700))[:3] == (30, 30, 200)
    assert img.getpixel((800, 700))[:3] == (200, 30, 30)


def test_compose_1144x267_visual_has_title_button_and_metaphor():
    spec = MANIFEST.templates["webinar_1144x267_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    img = _visual("webinar_1144x267_visual")
    assert img.size == (1144, 267)
    assert img.getpixel((750, 40))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1050, 20))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    assert img.getpixel((820, 205))[:3] == (0xCF, 0xF5, 0x00)  # yellow button
    # white-outline "Вебинар" badge in the black column
    assert _has(img.crop((30, 166, 290, 219)), (0xFF, 0xFF, 0xFF))
    # dark title glyphs on the green
    assert _has(img.crop((794, 69, 1108, 135)), (0x22, 0x22, 0x22))
    # metaphor rect x422..729 y-34..273 (both source halves present)
    box = img.crop((422, 0, 729, 267))
    assert _has(box, (30, 30, 200)) and _has(box, (200, 30, 30))


def test_compose_640x360_visual_has_title_button_and_metaphor():
    spec = MANIFEST.templates["webinar_640x360_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    img = _visual("webinar_640x360_visual")
    assert img.size == (640, 360)
    assert img.getpixel((610, 100))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((560, 30))[:3] == (0xC4, 0xDA, 0xF7)  # blue badge
    assert img.getpixel((300, 290))[:3] == (0xCF, 0xF5, 0x00)  # yellow button
    # dark title glyphs on the green
    assert _has(img.crop((293, 115, 602, 256)), (0x22, 0x22, 0x22))
    # metaphor rect x10..270 y80..340
    box = img.crop((10, 80, 270, 340))
    assert _has(box, (30, 30, 200)) and _has(box, (200, 30, 30))


def test_compose_1900x500_visual_has_left_column_and_yellow_button():
    spec = MANIFEST.templates["webinar_1900x500_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    img = _visual("webinar_1900x500_visual")
    assert img.size == (1900, 500)
    # body right of the metaphor (metaphor ends x1115, notch starts x1560)
    assert img.getpixel((1300, 60))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1700, 40))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    # notch_bl (x620..960 y420..500) sits fully under the metaphor rect -> layer assert
    assert any(layer.name == "notch_bl" for layer in spec.layers)
    assert img.getpixel((1250, 375))[:3] == (0xCF, 0xF5, 0x00)  # yellow button (left of label)
    # white-outline "Вебинар" badge + white date row in the black column
    assert _has(img.crop((50, 369, 445, 450)), (0xFF, 0xFF, 0xFF))
    assert _has(img.crop((50, 300, 610, 340)), (0xFF, 0xFF, 0xFF))
    # dark title glyphs on the green
    assert _has(img.crop((1115, 113, 1756, 250)), (0x22, 0x22, 0x22))
    # metaphor rect x582..1115 y-33..500
    box = img.crop((582, 0, 1115, 500))
    assert _has(box, (30, 30, 200)) and _has(box, (200, 30, 30))


def test_compose_1440x1080_rsy_visual_is_texture_and_metaphor_only():
    spec = MANIFEST.templates["webinar_1440x1080_rsy_visual"]
    names = {layer.name for layer in spec.layers}
    assert "title" not in names and "logo" not in names
    img = _visual("webinar_1440x1080_rsy_visual", texts={})
    assert img.size == (1440, 1080)
    assert img.getpixel((10, 10))[:3] == (0x22, 0x22, 0x22)  # dark margin
    # bright-green arrow-grid texture in the left margin strip
    assert _has(img.crop((35, 35, 165, 400)), (0x26, 0xD0, 0x7C))
    # metaphor rect x171..1269 y-15..1095 mirrored around mid 720
    assert img.getpixel((400, 540))[:3] == (30, 30, 200)
    assert img.getpixel((1100, 540))[:3] == (200, 30, 30)


def test_compose_1920x1080_vc_visual_has_badge_title_and_short_arrow():
    spec = MANIFEST.templates["webinar_1920x1080_vc_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    img = _visual(
        "webinar_1920x1080_vc_visual",
        texts={"title": "Как построить управляемый data lakehouse", **_V2_TEXTS},
    )
    assert img.size == (1920, 1080)
    assert img.getpixel((300, 500))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((40, 1000))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    # badge outline: black stroke box on the green
    assert _has(img.crop((111, 124, 351, 205)), (0x22, 0x22, 0x22))
    # dark title glyphs on the green
    assert _has(img.crop((111, 315, 1120, 700)), (0x22, 0x22, 0x22))
    # white date glyphs + green short arrow on the bottom notch
    assert _has(img.crop((60, 950, 590, 1040)), (0xFF, 0xFF, 0xFF))
    assert _has(img.crop((613, 946, 677, 1026)), (0x26, 0xD0, 0x7C))
    # metaphor rect x1031..1868 y200..1037 mirrored around mid 1449
    assert img.getpixel((1200, 600))[:3] == (30, 30, 200)
    assert img.getpixel((1700, 600))[:3] == (200, 30, 30)


def test_compose_1280x720_visual_has_brand_strip_and_no_speaker_extras():
    spec = MANIFEST.templates["webinar_1280x720_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "legal" not in names
    assert {"logo", "brand_ticks", "tagline", "title", "metaphor"} <= names
    img = _visual("webinar_1280x720_visual")
    assert img.size == (1280, 720)
    assert img.getpixel((300, 400))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1000, 100))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    assert img.getpixel((100, 680))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    # brand strip: gray tick lines + white tagline on the dark top band
    assert _has(img.crop((289, 21, 1062, 69)), (0x64, 0x64, 0x64))
    assert _has(img.crop((1092, 19, 1280, 75)), (0xFF, 0xFF, 0xFF))
    # dark title glyphs on the green
    assert _has(img.crop((85, 203, 300, 300)), (0x22, 0x22, 0x22))
    # metaphor rect x726..1264 y150..720 mirrored around mid 995
    assert img.getpixel((800, 400))[:3] == (30, 30, 200)
    assert img.getpixel((1150, 400))[:3] == (200, 30, 30)


def test_compose_2560x1440_visual_plain_metaphor_no_speaker_extras():
    spec = MANIFEST.templates["webinar_2560x1440_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    assert {"logo", "brand_ticks", "tagline", "title", "metaphor"} <= names
    # metaphor here is the UNFLIPPED full-frame placement (Figma node 3552:10912)
    metaphor = next(layer for layer in spec.layers if layer.name == "metaphor")
    assert metaphor.flip_h is False and metaphor.crop_scale == 1.0
    img = _visual("webinar_2560x1440_visual")
    assert img.size == (2560, 1440)
    assert img.getpixel((600, 800))[:3] == (0x26, 0xD0, 0x7C)  # green body
    # notch_tr strip above the (opaque test) metaphor rect y194+
    assert img.getpixel((2000, 180))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((200, 1350))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    assert _has(img.crop((544, 40, 2150, 130)), (0x64, 0x64, 0x64))  # ticks
    assert _has(img.crop((2148, 37, 2502, 137)), (0xFF, 0xFF, 0xFF))  # tagline
    # dark title glyphs on the green
    assert _has(img.crop((131, 467, 400, 1100)), (0x22, 0x22, 0x22))
    # metaphor rect x1349..2560(clipped) y194..1440, NOT mirrored: source red half stays left
    assert img.getpixel((1600, 800))[:3] == (200, 30, 30)
    assert img.getpixel((2400, 800))[:3] == (30, 30, 200)


def test_compose_1920x1080_advert_visual_badge_green_arrow_no_speaker_extras():
    spec = MANIFEST.templates["webinar_1920x1080_advert_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    assert {"logo", "brand_ticks", "tagline", "badge_box", "title", "date", "arrow", "time", "metaphor"} <= names
    img = _visual(
        "webinar_1920x1080_advert_visual",
        texts={"title": "Эволюция приложения в облаке", **_V2_TEXTS},
    )
    assert img.size == (1920, 1080)
    assert img.getpixel((300, 500))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1000, 200))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    assert img.getpixel((30, 1000))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    # brand strip: gray ticks + white tagline
    assert _has(img.crop((544, 40, 1510, 130)), (0x64, 0x64, 0x64))
    assert _has(img.crop((1566, 42, 1920, 142)), (0xFF, 0xFF, 0xFF))
    # outlined badge + dark title glyphs on the green
    assert _has(img.crop((111, 295, 351, 376)), (0x22, 0x22, 0x22))
    assert _has(img.crop((111, 420, 1100, 760)), (0x22, 0x22, 0x22))
    # white date/time + GREEN short arrow on the dark bottom notch
    assert _has(img.crop((60, 940, 600, 1035)), (0xFF, 0xFF, 0xFF))
    assert _has(img.crop((668, 940, 910, 1035)), (0xFF, 0xFF, 0xFF))
    assert _has(img.crop((614, 948, 674, 1022)), (0x26, 0xD0, 0x7C))
    # metaphor rect x1136..1820 y373..1057 mirrored around mid 1478
    assert img.getpixel((1300, 700))[:3] == (30, 30, 200)
    assert img.getpixel((1650, 700))[:3] == (200, 30, 30)


def test_compose_1920x1080_advert2_visual_no_data_row():
    spec = MANIFEST.templates["webinar_1920x1080_advert2_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    # advert2 has NO date/arrow/time row (Figma 3552:11059)
    assert "date" not in names and "time" not in names and "arrow" not in names
    assert {"logo", "brand_ticks", "tagline", "badge_box", "title", "metaphor"} <= names
    img = _visual(
        "webinar_1920x1080_advert2_visual",
        texts={"title": "Как построить управляемый data lakehouse", **_V2_TEXTS},
    )
    assert img.size == (1920, 1080)
    assert img.getpixel((300, 500))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1000, 200))[:3] == (0x22, 0x22, 0x22)  # notch_tr (h140)
    assert img.getpixel((30, 1000))[:3] == (0x22, 0x22, 0x22)  # notch_bl (y940 h140)
    assert img.getpixel((300, 930))[:3] == (0x26, 0xD0, 0x7C)  # green just above notch_bl
    # brand strip + outlined badge + dark title glyphs
    assert _has(img.crop((544, 40, 1510, 130)), (0x64, 0x64, 0x64))
    assert _has(img.crop((1566, 37, 1920, 137)), (0xFF, 0xFF, 0xFF))
    assert _has(img.crop((111, 295, 351, 376)), (0x22, 0x22, 0x22))
    assert _has(img.crop((111, 455, 1100, 880)), (0x22, 0x22, 0x22))
    # metaphor rect x1115..1864 y307..1056 mirrored around mid 1489
    assert img.getpixel((1300, 650))[:3] == (30, 30, 200)
    assert img.getpixel((1700, 650))[:3] == (200, 30, 30)


def test_compose_1600x900_vk_ad_visual_button_age_para_spacing():
    spec = MANIFEST.templates["webinar_1600x900_vk_ad_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "disclaimer" not in names
    assert "date" not in names and "time" not in names
    assert {"logo", "brand_ticks", "tagline", "badge_box", "title", "button_box", "button_label", "age", "metaphor"} <= names
    # title uses Figma paragraph spacing (three paragraphs, mb-[10px])
    title = next(layer for layer in spec.layers if layer.name == "title")
    assert title.para_spacing == 10
    img = _visual(
        "webinar_1600x900_vk_ad_visual",
        texts={"title": "Как построить управляемый data lakehouse", **_V2_TEXTS},
    )
    assert img.size == (1600, 900)
    assert img.getpixel((800, 180))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((300, 170))[:3] == (0x22, 0x22, 0x22)  # notch_tl
    assert img.getpixel((1000, 830))[:3] == (0x22, 0x22, 0x22)  # notch_br (y800)
    assert img.getpixel((1000, 780))[:3] == (0x26, 0xD0, 0x7C)  # green above notch_br
    # brand strip: gray ticks + white tagline + white logo ink
    assert _has(img.crop((385, 28, 1397, 92)), (0x64, 0x64, 0x64))
    assert _has(img.crop((1260, 26, 1560, 96)), (0xFF, 0xFF, 0xFF))
    # outlined badge + dark title glyphs on the green
    assert _has(img.crop((860, 209, 1032, 278)), (0x22, 0x22, 0x22))
    assert _has(img.crop((860, 320, 1400, 610)), (0x22, 0x22, 0x22))
    # yellow register button with dark label
    assert img.getpixel((900, 690))[:3] == (0xCF, 0xF5, 0x00)
    assert _has(img.crop((880, 650, 1490, 740)), (0x22, 0x22, 0x22))
    # gray age mark over the metaphor
    assert _has(img.crop((40, 796, 120, 856)), (0xCA, 0xCA, 0xCA))
    # metaphor rect x40..741 y200..860, NOT mirrored: red half left, blue right
    assert img.getpixel((200, 500))[:3] == (200, 30, 30)
    assert img.getpixel((600, 500))[:3] == (30, 30, 200)


# --- visual variant, family V3 (story/email/podborki, board 3552:11362) --------


def _greenish(px) -> bool:
    r, g, b = px[:3]
    return r < 90 and g > 150 and 60 < b < 190


def test_compose_1080x1920_story_visual_headers_and_dots():
    spec = MANIFEST.templates["webinar_1080x1920_story_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "legal" not in names
    assert {"header_a", "header_b", "title", "dots_bl", "metaphor"} <= names
    img = _visual("webinar_1080x1920_story_visual")
    assert img.size == (1080, 1920)
    assert img.getpixel((500, 350))[:3] == (0x26, 0xD0, 0x7C)  # green body
    assert img.getpixel((1000, 400))[:3] == (0x22, 0x22, 0x22)  # notch_tr
    assert img.getpixel((80, 1700))[:3] == (0x22, 0x22, 0x22)  # notch_bl
    # header: dark "Вебинар"/date-time glyphs + white diagonal arrows on green
    assert _has(img.crop((115, 260, 515, 312)), (0x22, 0x22, 0x22))
    assert _has(img.crop((320, 260, 930, 312)), (0xFF, 0xFF, 0xFF))
    # dark title glyphs
    assert _has(img.crop((115, 375, 850, 835)), (0x22, 0x22, 0x22))
    # white dots grid over the metaphor
    assert _has(img.crop((211, 1567, 459, 1815)), (0xFF, 0xFF, 0xFF))
    # metaphor rect x60..1020 y675..1860 mirrored: blue left, red right
    assert img.getpixel((300, 1200))[:3] == (30, 30, 200)
    assert img.getpixel((900, 1200))[:3] == (200, 30, 30)


_EM_TEXTS = {"title": "Открытый диалог с партнерами", "date": "23 июня", "time": "13:00"}


def test_compose_1200x600_email1_visual_event_branding():
    spec = MANIFEST.templates["webinar_1200x600_email1_visual"]
    names = {layer.name for layer in spec.layers}
    assert "subtitle" not in names and "legal" not in names
    assert {"media", "arrow_under", "metaphor", "arrow_over", "strip_tr", "strip_bl",
            "badge_box", "badge", "divider_top", "title", "divider_footer",
            "date", "arrow", "time"} <= names
    # event board carries "Мероприятия"; Глеб decided (2026-07-21) to swap the
    # caption to "Вебинар" for the webinar scenario, pill shrunk to the shared
    # 173px width (email-2/3/4). Mint #8EE7BB color kept per the board.
    badge = next(layer for layer in spec.layers if layer.name == "badge")
    assert badge.fixed_content == "Вебинар"
    img = _visual("webinar_1200x600_email1_visual", texts=_EM_TEXTS)
    assert img.size == (1200, 600)
    assert img.getpixel((580, 300))[:3] == (0x22, 0x22, 0x22)  # dark left half
    assert img.getpixel((70, 84))[:3] == (0x8E, 0xE7, 0xBB)  # mint badge box
    assert _has(img.crop((80, 62, 225, 106)), (0x22, 0x22, 0x22))  # badge glyphs
    assert img.getpixel((250, 84))[:3] == (0x22, 0x22, 0x22)  # dark past the 173px pill
    assert img.getpixel((100, 138))[:3] == (0xFF, 0xFF, 0xFF)  # white divider_top
    assert img.getpixel((100, 484))[:3] == (0xFF, 0xFF, 0xFF)  # white divider_footer
    assert _has(img.crop((60, 237, 552, 437)), (0xFF, 0xFF, 0xFF))  # title ink
    assert _has(img.crop((60, 508, 240, 548)), (0xFF, 0xFF, 0xFF))  # date ink
    assert _has(img.crop((440, 508, 540, 548)), (0xFF, 0xFF, 0xFF))  # time ink
    assert _greenish(img.getpixel((300, 527)))  # green footer arrow shaft
    # corner strips over the media
    assert img.getpixel((1000, 10))[:3] == (0x22, 0x22, 0x22)
    assert img.getpixel((700, 590))[:3] == (0x22, 0x22, 0x22)
    # decorative over-arrow drawn on top of the metaphor
    px = img.getpixel((620, 427))[:3]
    assert max(px) < 90
    # metaphor rect x600..1200 mirrored: blue left, red right
    assert img.getpixel((700, 300))[:3] == (30, 30, 200)
    assert img.getpixel((1100, 300))[:3] == (200, 30, 30)


def test_compose_1200x600_email2_visual_footer_row():
    spec = MANIFEST.templates["webinar_1200x600_email2_visual"]
    names = {layer.name for layer in spec.layers}
    assert {"media", "metaphor", "notch_l", "notch_r", "badge", "divider_top",
            "title", "divider_footer", "date", "arrow", "time"} <= names
    img = _visual("webinar_1200x600_email2_visual", texts=_EM_TEXTS)
    assert img.size == (1200, 600)
    assert img.getpixel((580, 300))[:3] == (0x22, 0x22, 0x22)  # dark left half
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)  # green badge pill
    assert img.getpixel((100, 145))[:3] == (0x26, 0xD0, 0x7C)  # green divider
    assert _has(img.crop((60, 166, 540, 472)), (0xFF, 0xFF, 0xFF))  # title ink
    assert _has(img.crop((60, 511, 300, 551)), (0xFF, 0xFF, 0xFF))  # date ink
    assert _greenish(img.getpixel((350, 527)))  # footer arrow shaft
    assert img.getpixel((610, 70))[:3] == (0x22, 0x22, 0x22)  # notch_l
    assert img.getpixel((1180, 530))[:3] == (0x22, 0x22, 0x22)  # notch_r
    # metaphor mirrored: blue left, red right
    assert img.getpixel((700, 300))[:3] == (30, 30, 200)
    assert img.getpixel((1050, 300))[:3] == (200, 30, 30)


def test_compose_1200x600_email3_visual_button():
    spec = MANIFEST.templates["webinar_1200x600_email3_visual"]
    names = {layer.name for layer in spec.layers}
    assert "date" not in names and "time" not in names
    assert {"media", "metaphor", "badge", "divider_top", "title",
            "button_box", "button_label"} <= names
    img = _visual("webinar_1200x600_email3_visual", texts=_EM_TEXTS)
    assert img.size == (1200, 600)
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)  # green badge pill
    assert _has(img.crop((60, 178, 540, 434)), (0xFF, 0xFF, 0xFF))  # title ink
    # white "Смотреть запись" button with dark label
    assert img.getpixel((100, 490))[:3] == (0xFF, 0xFF, 0xFF)
    assert _has(img.crop((65, 462, 535, 537)), (0x22, 0x22, 0x22))
    # metaphor mirrored: blue left, red right
    assert img.getpixel((700, 300))[:3] == (30, 30, 200)
    assert img.getpixel((1100, 300))[:3] == (200, 30, 30)


def test_compose_1200x600_email4_visual_title_only():
    spec = MANIFEST.templates["webinar_1200x600_email4_visual"]
    names = {layer.name for layer in spec.layers}
    assert "date" not in names and "time" not in names
    assert "button_box" not in names
    assert {"media", "metaphor", "badge", "divider_top", "title", "divider_bottom"} <= names
    img = _visual("webinar_1200x600_email4_visual", texts=_EM_TEXTS)
    assert img.size == (1200, 600)
    assert img.getpixel((70, 70))[:3] == (0x26, 0xD0, 0x7C)  # green badge pill
    assert _has(img.crop((60, 182, 552, 543)), (0xFF, 0xFF, 0xFF))  # title ink
    assert img.getpixel((100, 539))[:3] == (0x26, 0xD0, 0x7C)  # green divider_bottom
    # metaphor mirrored: blue left, red right
    assert img.getpixel((700, 300))[:3] == (30, 30, 200)
    assert img.getpixel((1100, 300))[:3] == (200, 30, 30)


def test_compose_240x240_podborki_visual_metaphor_only():
    spec = MANIFEST.templates["webinar_240x240_email_podborki_visual"]
    names = {layer.name for layer in spec.layers}
    assert names == {"metaphor"}
    img = _visual("webinar_240x240_email_podborki_visual", texts={"title": "Т"})
    assert img.size == (240, 240)
    assert img.getpixel((0, 120))[:3] == (0x22, 0x22, 0x22)  # 1px dark gutter
    # metaphor rect x1..239 mirrored: blue left, red right
    assert img.getpixel((60, 120))[:3] == (30, 30, 200)
    assert img.getpixel((180, 120))[:3] == (200, 30, 30)
