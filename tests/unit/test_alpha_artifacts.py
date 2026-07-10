"""Block 1 (2026-07-10): alpha-channel layer artifacts.

Besides the final composite, every banner ships three transparent-PNG
artifacts inside the result ZIP:
  - message  — text plates only (slogan/subtitle/CTA) on a transparent canvas,
  - hero     — the hero frame (cutout+frame+dots for render; the photo as-is,
               cover-cropped, WITHOUT any cutout — «так задумано»),
  - brand    — the brand header/footer strips («третьим слоем»).

Contract under test:
  1. infra.composer.compose(..., only={...}) renders only the requested layer
     group on a fully transparent canvas; filtered-out hero layers never raise
     on hero=None.
  2. fill_templates_per_format entries carry {"layers": {message, hero, brand}}
     pointing at existing alpha PNGs.
  3. render_all._build_zip_sync adds them under layers/<format>_<name>.png so
     the results-dir top-level *.png glob (final grid) stays composite-only.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from graph.nodes import fill_templates_per_format as fill_mod
from graph.nodes.render_all import _build_zip_sync
from infra.composer import compose
from infra.template_manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest(REPO_ROOT / "config" / "templates.json")

_TEXTS = {"slogan": "Слоган про облако", "subtitle": "Подзаголовок", "cta": "Попробовать"}


def _solid_hero(color=(255, 0, 255, 255)) -> Image.Image:
    return Image.new("RGBA", (400, 400), color)


# ----- composer `only` filter -------------------------------------------------


def test_only_message_transparent_canvas_and_no_hero_needed():
    """Message-only artifact: text plates on transparent canvas; a spec with a
    hero layer must NOT raise on hero=None because the layer is filtered out."""
    spec = MANIFEST.templates["banner_300x600_render"]
    img = compose(
        spec, hero=None, texts=_TEXTS, assets_root=REPO_ROOT, only={"message"}
    )
    assert img.size == (300, 600)
    # corners transparent — no background fill, no header, no frame
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((299, 0))[3] == 0
    # CTA plate (white bg at y512..556) is part of the message group;
    # probe off-center so a centered glyph can't sit under the pixel
    assert img.getpixel((30, 534)) == (255, 255, 255, 255)
    # hero zone stays empty (no dots, no cutout)
    assert img.getpixel((150, 200))[3] == 0


def test_only_hero_render_has_frame_but_no_header_no_text():
    spec = MANIFEST.templates["banner_300x600_render"]
    img = compose(
        spec, hero=_solid_hero(), texts=_TEXTS, assets_root=REPO_ROOT, only={"hero"}
    )
    # green frame bar (x0..10 for y40..600) belongs to the hero group
    assert img.getpixel((2, 300))[:3] == (0x26, 0xD0, 0x7C)
    # header strip region (y<40, right of the top tab) transparent — brand excluded
    assert img.getpixel((150, 10))[3] == 0
    # CTA plate region must NOT be the white plate (message excluded)
    assert img.getpixel((30, 534)) != (255, 255, 255, 255)


def test_only_hero_photo_is_uncropped_cover_photo():
    """Photo scenario hero artifact = the photo as-is (cover), no cutout."""
    spec = MANIFEST.templates["banner_300x600_photo"]
    img = compose(
        spec,
        hero=_solid_hero((10, 20, 30, 255)),
        texts=_TEXTS,
        assets_root=REPO_ROOT,
        only={"hero"},
    )
    # hero rect is (0,50)-(300,600) cover → solid photo pixel mid-frame
    assert img.getpixel((150, 300)) == (10, 20, 30, 255)
    # header band excluded → transparent
    assert img.getpixel((150, 25))[3] == 0


def test_only_brand_photo_header_footer_only():
    spec = MANIFEST.templates["banner_300x600_photo"]
    img = compose(
        spec, hero=None, texts=_TEXTS, assets_root=REPO_ROOT, only={"brand"}
    )
    # header (y0..50) and legal footer (y586..600) present
    assert img.getpixel((150, 25))[3] > 0
    assert img.getpixel((150, 592))[3] > 0
    # middle of the banner transparent (no hero, no text, no scrim)
    assert img.getpixel((150, 300))[3] == 0


def test_only_none_keeps_default_composite():
    """Without `only` the composite is unchanged: opaque background fill."""
    spec = MANIFEST.templates["banner_300x600_render"]
    img = compose(spec, hero=_solid_hero(), texts=_TEXTS, assets_root=REPO_ROOT)
    assert img.getpixel((150, 10))[3] == 255  # header drawn


# ----- fill_templates_per_format entries carry layer artifacts ----------------


def _ranked(n: int) -> list[dict]:
    return [
        {
            "id": f"c{i}",
            "slogan": f"Слоган {i}",
            "body": f"Подзаголовок {i}",
            "cta": f"Кнопка {i}",
            "hook_angle": "rational",
            "score": float(n - i),
            "reason": "r",
        }
        for i in range(n)
    ]


def _heroes(tmp_path: Path, scenarios: list[str]) -> list[dict]:
    out = []
    for i, scen in enumerate(scenarios):
        p = tmp_path / f"hero_{i}.png"
        Image.new("RGBA", (400, 400), (180, 90, 90, 255)).save(p)
        out.append(
            {
                "url": None,
                "local_path": str(p),
                "style": scen,
                "variant": "default",
                "prompt": f"prompt {i}",
            }
        )
    return out


@pytest.mark.asyncio
async def test_node_entries_carry_three_alpha_layer_files(monkeypatch, tmp_path):
    monkeypatch.setattr(fill_mod, "_RENDER_DIR", tmp_path / "renders")
    scenarios = ["render", "photo"]
    state = {
        "session_id": "sX",
        "ranked": _ranked(2),
        "scenarios": scenarios,
        "generated_heroes": _heroes(tmp_path, scenarios),
    }
    out = await fill_mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    for entry in out["rendered_files"]:
        layers = entry["layers"]
        assert set(layers) == {"message", "hero", "brand"}
        # per-group probe of a pixel that must be transparent on the alpha
        # canvas: message → top-left corner, hero → header band (above the
        # frame/photo), brand → mid-banner (between header and footer strips)
        probes = {"message": (0, 0), "hero": (150, 10), "brand": (150, 300)}
        for name, path in layers.items():
            img = Image.open(path)
            assert img.mode == "RGBA", name
            assert img.getpixel(probes[name])[3] == 0, name
        # message artifact ≠ hero artifact ≠ composite
        assert len({entry["path"], *layers.values()}) == 4


# ----- render_all ZIP packs layer artifacts under layers/ ---------------------


def _fake_png(path: Path) -> str:
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(path)
    return str(path)


def test_build_zip_includes_layer_artifacts(tmp_path):
    files = [
        {
            "format": "01_render",
            "path": _fake_png(tmp_path / "b1.png"),
            "layers": {
                "message": _fake_png(tmp_path / "b1_message.png"),
                "hero": _fake_png(tmp_path / "b1_hero.png"),
                "brand": _fake_png(tmp_path / "b1_brand.png"),
            },
        },
        {"format": "02_photo", "path": _fake_png(tmp_path / "b2.png")},
    ]
    zip_path = tmp_path / "out.zip"
    _build_zip_sync(zip_path, files)
    names = set(zipfile.ZipFile(zip_path).namelist())
    assert "01_render.png" in names
    assert "02_photo.png" in names
    assert "layers/01_render_message.png" in names
    assert "layers/01_render_hero.png" in names
    assert "layers/01_render_brand.png" in names
    # entry without layers adds nothing extra
    assert not any(n.startswith("layers/02_photo") for n in names)
