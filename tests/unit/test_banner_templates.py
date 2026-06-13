"""Unit tests for the M4.1 /banner manifest (config/banner_templates.json).

Validates the on-disk banner config loads, the webinar_visual scenario is
wired correctly, every scenario format references baked-plate + hero +
slot layers consistently, and the declared slot keys match the text layers.
"""

from __future__ import annotations

from pathlib import Path

from infra.template_manifest import ImageLayer, TextLayer, load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
BANNER_MANIFEST = REPO_ROOT / "config" / "banner_templates.json"


def test_banner_manifest_loads():
    m = load_manifest(BANNER_MANIFEST)
    assert m.scenarios, "expected at least one scenario"


def test_webinar_visual_scenario():
    m = load_manifest(BANNER_MANIFEST)
    sc = m.scenarios["webinar_visual"]
    assert len(sc.formats) == 24
    assert set(sc.slots) == {"title", "date", "time", "datetime"}
    assert sc.hero is not None
    assert sc.hero.remove_bg is True
    # every referenced format exists (the manifest validator also enforces this)
    for fmt in sc.formats:
        assert fmt in m.templates


def test_every_format_has_one_plate_and_hero():
    m = load_manifest(BANNER_MANIFEST)
    for fmt in m.scenarios["webinar_visual"].formats:
        spec = m.templates[fmt]
        plates = [l for l in spec.layers if isinstance(l, ImageLayer)]
        heroes = [l for l in spec.layers if l.type == "hero_cutout"]
        assert len(plates) == 1, f"{fmt} should have exactly one plate"
        assert len(heroes) == 1, f"{fmt} should have exactly one hero_cutout"
        assert plates[0].path.endswith("_bg.png")


def test_all_plate_files_exist():
    m = load_manifest(BANNER_MANIFEST)
    for sc in m.scenarios.values():
        for fmt in sc.formats:
            for layer in m.templates[fmt].layers:
                if isinstance(layer, ImageLayer):
                    assert (REPO_ROOT / layer.path).exists(), f"{fmt}: missing {layer.path}"


def test_smm_covers_scenario():
    m = load_manifest(BANNER_MANIFEST)
    sc = m.scenarios["smm_covers"]
    assert sc.formats == [
        "smm_cover_visual",
        "smm_cover_speaker",
        "smm_cover_announce",
        "smm_cover_text",
    ]
    assert sc.hero is not None and sc.hero.remove_bg is True
    # text archetype is hero-less
    text = m.templates["smm_cover_text"]
    assert not any(l.type in ("hero", "hero_cutout") for l in text.layers)
    # visual is a framed cover photo (HeroLayer, keeps its bg);
    # speaker/announce are bg-removed cutouts
    assert any(l.type == "hero" for l in m.templates["smm_cover_visual"].layers)
    for fmt in ("smm_cover_speaker", "smm_cover_announce"):
        assert any(l.type == "hero_cutout" for l in m.templates[fmt].layers)


def test_text_layers_use_declared_slots():
    m = load_manifest(BANNER_MANIFEST)
    declared = set(m.scenarios["webinar_visual"].slots)
    for fmt in m.scenarios["webinar_visual"].formats:
        for layer in m.templates[fmt].layers:
            if isinstance(layer, TextLayer) and layer.slot is not None:
                assert layer.slot in declared, f"{fmt}: slot {layer.slot!r} not declared"


def test_plate_is_bottom_layer():
    m = load_manifest(BANNER_MANIFEST)
    for fmt in m.scenarios["webinar_visual"].formats:
        spec = m.templates[fmt]
        plate = next(l for l in spec.layers if isinstance(l, ImageLayer))
        others = [l for l in spec.layers if l is not plate]
        assert all(l.z >= plate.z for l in others), f"{fmt}: plate must be under all layers"
