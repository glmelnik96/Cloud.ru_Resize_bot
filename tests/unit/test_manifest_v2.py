"""Unit tests for M4.0 manifest v2 + composer extensions.

Coverage:
- HeroCutoutLayer parsing defaults,
- TextLayer slot/fixed_content validation,
- ScenarioSpec + HeroPolicy parsing, unknown-format validation,
- apply_variant deep-merge by layer name (incl. nested fields),
- composer: hero_cutout anchoring + alpha, fixed_content, optional hero,
- backward compat: production config/templates.json still loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from infra.composer import compose
from infra.template_manifest import (
    HeroCutoutLayer,
    ScenarioSpec,
    TemplateManifest,
    TemplateSpec,
    TextLayer,
    VariantSpec,
    apply_variant,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "templates.json"


# ----- manifest models --------------------------------------------------------


def test_hero_cutout_defaults():
    layer = HeroCutoutLayer.model_validate(
        {"type": "hero_cutout", "x": 0, "y": 0, "width": 100, "height": 100}
    )
    assert layer.anchor_h == "center"
    assert layer.anchor_v == "bottom"
    assert layer.allow_upscale is True


def test_text_layer_fixed_content_without_slot():
    layer = TextLayer.model_validate(
        {
            "type": "text",
            "fixed_content": "Зарегистрироваться",
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 30,
            "font_family": "SBSansDisplay",
            "font_size_max": 15,
            "color": "#000000",
        }
    )
    assert layer.slot is None
    assert layer.fixed_content == "Зарегистрироваться"


def test_text_layer_requires_slot_or_fixed_content():
    with pytest.raises(ValidationError, match="slot or fixed_content"):
        TextLayer.model_validate(
            {
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 30,
                "font_family": "SBSansDisplay",
                "font_size_max": 15,
                "color": "#000000",
            }
        )


def test_box_background_outline_only():
    """Webinar 'Вебинар' badge: 2px border, no fill."""
    layer = TextLayer.model_validate(
        {
            "type": "text",
            "fixed_content": "Вебинар",
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 40,
            "font_family": "SBSansDisplay",
            "font_size_max": 20,
            "color": "#222222",
            "background": {"border_color": "#222222", "border_width": 2},
        }
    )
    assert layer.background is not None
    assert layer.background.color is None
    assert layer.background.border_width == 2


def test_box_background_needs_fill_or_border():
    with pytest.raises(ValidationError, match="fill color or a border"):
        TextLayer.model_validate(
            {
                "type": "text",
                "fixed_content": "x",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 40,
                "font_family": "SBSansDisplay",
                "font_size_max": 20,
                "color": "#222222",
                "background": {"radius": 4},
            }
        )


def _minimal_template() -> dict:
    return {
        "width": 100,
        "height": 100,
        "background_color": "#222222",
        "layers": [],
    }


def test_scenarios_parse_with_hero_policy():
    m = TemplateManifest.model_validate(
        {
            "version": "0.7.0",
            "templates": {"fmt_a": _minimal_template()},
            "scenarios": {
                "webinar_visual": {
                    "title": "Вебинар с вижуалом",
                    "formats": ["fmt_a"],
                    "slots": ["title", "date"],
                    "hero": {"source": "both", "remove_bg": True},
                }
            },
        }
    )
    sc = m.scenarios["webinar_visual"]
    assert sc.hero is not None
    assert sc.hero.remove_bg is True
    assert sc.variants == []


def test_scenario_without_hero_is_valid():
    sc = ScenarioSpec.model_validate(
        {"title": "Text cover", "formats": ["fmt_a"], "slots": ["title"]}
    )
    assert sc.hero is None


def test_scenario_unknown_format_rejected():
    with pytest.raises(ValidationError, match="unknown formats"):
        TemplateManifest.model_validate(
            {
                "version": "0.7.0",
                "templates": {"fmt_a": _minimal_template()},
                "scenarios": {
                    "bad": {"title": "Bad", "formats": ["fmt_a", "fmt_missing"]}
                },
            }
        )


def test_production_manifest_still_loads():
    m = load_manifest(MANIFEST)
    assert m.templates
    # scenarios are optional and absent in the M3 manifest
    assert m.scenarios == {}


# ----- apply_variant ----------------------------------------------------------


def _spec_with_named_layers() -> TemplateSpec:
    return TemplateSpec.model_validate(
        {
            "width": 100,
            "height": 100,
            "layers": [
                {
                    "type": "image",
                    "name": "accent_strip",
                    "path": "assets/brand/strip_green.png",
                    "x": 0,
                    "y": 90,
                    "width": 100,
                    "height": 10,
                },
                {
                    "type": "text",
                    "name": "cta_button",
                    "slot": "cta",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 30,
                    "font_family": "SBSansDisplay",
                    "font_size_max": 15,
                    "color": "#000000",
                    "background": {"color": "#CFF500", "radius": 4},
                },
                {
                    "type": "text",
                    "slot": "title",
                    "x": 0,
                    "y": 40,
                    "width": 100,
                    "height": 30,
                    "font_family": "SBSansDisplay",
                    "font_size_max": 15,
                    "color": "#FFFFFF",
                },
            ],
        }
    )


def test_apply_variant_overrides_by_name():
    spec = _spec_with_named_layers()
    variant = VariantSpec(
        id="purple",
        overrides={
            "accent_strip": {"path": "assets/brand/strip_purple.png"},
            "cta_button": {"background": {"color": "#B388FF"}},
        },
    )
    out = apply_variant(spec, variant)
    assert out.layers[0].path == "assets/brand/strip_purple.png"
    # nested merge keeps sibling field (radius) intact
    assert out.layers[1].background.color == "#B388FF"
    assert out.layers[1].background.radius == 4
    # unnamed layer untouched
    assert out.layers[2].color == "#FFFFFF"
    # original spec not mutated
    assert spec.layers[0].path == "assets/brand/strip_green.png"
    assert spec.layers[1].background.color == "#CFF500"


def test_apply_variant_no_overrides_returns_same_spec():
    spec = _spec_with_named_layers()
    assert apply_variant(spec, VariantSpec(id="noop")) is spec


def test_apply_variant_bad_override_fails_loudly():
    spec = _spec_with_named_layers()
    variant = VariantSpec(id="broken", overrides={"accent_strip": {"width": -5}})
    with pytest.raises(ValidationError):
        apply_variant(spec, variant)


# ----- composer: hero_cutout --------------------------------------------------


def _cutout_hero(w: int = 50, h: int = 50) -> Image.Image:
    """Opaque red square with a fully transparent 10px top band."""
    img = Image.new("RGBA", (w, h), (255, 0, 0, 255))
    transparent = Image.new("RGBA", (w, 10), (0, 0, 0, 0))
    img.paste(transparent, (0, 0))
    return img


def _cutout_spec(**layer_kwargs) -> TemplateSpec:
    layer = {
        "type": "hero_cutout",
        "x": 0,
        "y": 0,
        "width": 100,
        "height": 100,
        **layer_kwargs,
    }
    return TemplateSpec.model_validate(
        {"width": 100, "height": 100, "background_color": "#0000FF", "layers": [layer]}
    )


def test_hero_cutout_bottom_center_no_upscale():
    spec = _cutout_spec(allow_upscale=False)
    out = compose(spec, hero=_cutout_hero(), texts={}, assets_root=REPO_ROOT)
    # 50x50 hero anchored bottom-center in 100x100: occupies x 25..74, y 50..99
    assert out.getpixel((50, 95)) == (255, 0, 0, 255)  # inside cutout
    assert out.getpixel((50, 5)) == (0, 0, 255, 255)  # above cutout: canvas bg
    assert out.getpixel((5, 95)) == (0, 0, 255, 255)  # left of cutout: canvas bg


def test_hero_cutout_alpha_lets_background_through():
    spec = _cutout_spec(allow_upscale=False)
    out = compose(spec, hero=_cutout_hero(), texts={}, assets_root=REPO_ROOT)
    # transparent top band of the cutout (y 50..59 on canvas) shows bg
    assert out.getpixel((50, 55)) == (0, 0, 255, 255)


def test_hero_cutout_upscales_by_default():
    spec = _cutout_spec()  # allow_upscale=True
    out = compose(spec, hero=_cutout_hero(), texts={}, assets_root=REPO_ROOT)
    # scaled 2x: transparent band is y 0..19, opaque from y 20
    assert out.getpixel((50, 30)) == (255, 0, 0, 255)
    assert out.getpixel((50, 5)) == (0, 0, 255, 255)


def test_hero_cutout_anchor_left_top():
    spec = _cutout_spec(anchor_h="left", anchor_v="top", allow_upscale=False)
    out = compose(spec, hero=_cutout_hero(), texts={}, assets_root=REPO_ROOT)
    assert out.getpixel((5, 25)) == (255, 0, 0, 255)  # left-top region (below band)
    assert out.getpixel((95, 95)) == (0, 0, 255, 255)  # bottom-right: canvas bg


# ----- composer: fixed_content + optional hero --------------------------------


def test_fixed_content_drawn_without_texts():
    spec = TemplateSpec.model_validate(
        {
            "width": 200,
            "height": 60,
            "background_color": "#FFFFFF",
            "layers": [
                {
                    "type": "text",
                    "fixed_content": "Зарегистрироваться",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 60,
                    "font_family": "SBSansDisplay",
                    "font_weight": "Semibold",
                    "font_size_max": 20,
                    "color": "#000000",
                    "align_h": "center",
                    "align_v": "middle",
                    "max_lines": 1,
                }
            ],
        }
    )
    out = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    non_white = sum(
        1 for px in out.getdata() if px[:3] != (255, 255, 255)
    )
    assert non_white > 50  # glyphs were actually drawn


def test_compose_without_hero_ok_when_no_hero_layers():
    spec = TemplateSpec.model_validate(
        {"width": 50, "height": 50, "background_color": "#222222", "layers": []}
    )
    out = compose(spec, hero=None, texts={}, assets_root=REPO_ROOT)
    assert out.size == (50, 50)


def test_compose_hero_none_with_hero_layer_raises():
    spec = _cutout_spec()
    with pytest.raises(ValueError, match="hero=None"):
        compose(spec, hero=None, texts={}, assets_root=REPO_ROOT, slug="fmt_x")
