"""Unit tests for the new M3.3 template manifest parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from infra.template_manifest import (
    BoxBackground,
    HeroLayer,
    ImageLayer,
    PerLineHighlight,
    TemplateManifest,
    TemplateSpec,
    TextLayer,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST = REPO_ROOT / "config" / "templates.json"


# ----- Discriminated-union parsing -------------------------------------------


def test_layer_discriminator_picks_image():
    spec = TemplateSpec.model_validate(
        {
            "width": 100,
            "height": 100,
            "layers": [
                {
                    "type": "image",
                    "path": "assets/brand/x.png",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 20,
                }
            ],
        }
    )
    assert isinstance(spec.layers[0], ImageLayer)
    assert spec.layers[0].path == "assets/brand/x.png"


def test_layer_discriminator_picks_hero():
    spec = TemplateSpec.model_validate(
        {
            "width": 100,
            "height": 100,
            "layers": [
                {
                    "type": "hero",
                    "x": 0,
                    "y": 20,
                    "width": 100,
                    "height": 60,
                    "fit": "contain",
                }
            ],
        }
    )
    assert isinstance(spec.layers[0], HeroLayer)
    assert spec.layers[0].fit == "contain"


def test_layer_discriminator_picks_text_with_nested_boxes():
    spec = TemplateSpec.model_validate(
        {
            "width": 100,
            "height": 100,
            "layers": [
                {
                    "type": "text",
                    "slot": "slogan",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 30,
                    "font_family": "SBSansDisplay",
                    "font_weight": "Semibold",
                    "font_size_max": 18,
                    "color": "#FFFFFF",
                    "per_line_highlight": {
                        "color": "#222222",
                        "padding_x": 4,
                        "padding_y": 2,
                    },
                    "background": {"color": "#CFF500", "radius": 4},
                }
            ],
        }
    )
    layer = spec.layers[0]
    assert isinstance(layer, TextLayer)
    assert isinstance(layer.per_line_highlight, PerLineHighlight)
    assert isinstance(layer.background, BoxBackground)
    assert layer.per_line_highlight.padding_x == 4
    assert layer.background.color == "#CFF500"


# ----- Validation errors ------------------------------------------------------


def test_unknown_layer_type_rejected():
    with pytest.raises(ValidationError):
        TemplateSpec.model_validate(
            {
                "width": 100,
                "height": 100,
                "layers": [{"type": "video", "x": 0, "y": 0}],
            }
        )


def test_unknown_text_slot_rejected():
    with pytest.raises(ValidationError):
        TemplateSpec.model_validate(
            {
                "width": 100,
                "height": 100,
                "layers": [
                    {
                        "type": "text",
                        "slot": "footnote",
                        "x": 0,
                        "y": 0,
                        "width": 10,
                        "height": 10,
                        "font_family": "SBSansDisplay",
                        "font_size_max": 12,
                        "color": "#000",
                    }
                ],
            }
        )


def test_negative_dimensions_rejected():
    with pytest.raises(ValidationError):
        TemplateSpec.model_validate(
            {
                "width": 0,
                "height": 100,
                "layers": [],
            }
        )


# ----- Real manifest fixture --------------------------------------------------


def test_real_manifest_loads():
    manifest = load_manifest(REAL_MANIFEST)
    assert manifest.version == "0.6.0"
    assert set(manifest.templates) == {
        "banner_240x400",
        "banner_300x250",
        "banner_300x500",
    }


def test_real_manifest_each_template_has_required_slots():
    """Every template must have: 1 hero, 1 brand image, slogan, cta, age_rating."""
    manifest = load_manifest(REAL_MANIFEST)
    for slug, spec in manifest.templates.items():
        layer_types = [type(layer).__name__ for layer in spec.layers]
        assert "HeroLayer" in layer_types, f"{slug}: no hero"
        assert "ImageLayer" in layer_types, f"{slug}: no brand image"
        text_slots = {
            layer.slot for layer in spec.layers if isinstance(layer, TextLayer)
        }
        assert text_slots == {"slogan", "cta", "age_rating"}, (
            f"{slug}: text slots = {text_slots}"
        )


def test_real_manifest_z_order_is_ascending_or_explicit():
    """No requirement that z is strictly increasing, but z must be set so
    composer can sort deterministically."""
    manifest = load_manifest(REAL_MANIFEST)
    for spec in manifest.templates.values():
        zs = [layer.z for layer in spec.layers]
        assert all(isinstance(z, int) for z in zs)


def test_real_manifest_brand_image_dimensions_match_frame_width():
    """Brand area line spans the whole canvas width."""
    manifest = load_manifest(REAL_MANIFEST)
    for slug, spec in manifest.templates.items():
        brand = next(
            (layer for layer in spec.layers if isinstance(layer, ImageLayer)),
            None,
        )
        assert brand is not None
        assert brand.width == spec.width, (
            f"{slug}: brand width {brand.width} != canvas {spec.width}"
        )


def test_corrupt_manifest_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_manifest(path)


def test_missing_manifest_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "no_such.json")
