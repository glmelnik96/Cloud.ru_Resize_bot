"""Unit tests for infra.banner_render (M4 scenario renderer).

Uses a synthetic in-memory manifest (no static plate files) so the tests
do not depend on designer-exported PNGs. Covers: base render, variant
fan-out, hero-less scenario, per-format isolation, and ZIP bundling.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from infra.banner_render import _with_derived_slots, build_zip, render_scenario
from infra.template_manifest import TemplateManifest


def test_datetime_slot_derivation():
    assert _with_derived_slots({"date": "2 июля", "time": "11:00"})["datetime"] == "2 июля в 11:00"
    assert _with_derived_slots({"date": "2 июля"})["datetime"] == "2 июля"
    assert _with_derived_slots({"time": "11:00"})["datetime"] == "11:00"
    # caller-supplied datetime is not overwritten
    assert _with_derived_slots({"date": "x", "time": "y", "datetime": "Z"})["datetime"] == "Z"
    assert "datetime" not in _with_derived_slots({"title": "t"})


def test_speaker_slot_derivation():
    out = _with_derived_slots({"speaker_name": "Вера Орлова", "speaker_role": "Менеджер"})
    assert out["speaker"] == "Вера Орлова\nМенеджер"
    assert _with_derived_slots({"speaker_name": "Вера"})["speaker"] == "Вера"
    assert "speaker" not in _with_derived_slots({"title": "t"})


def _tiny_hero() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (200, 200), (0, 200, 120, 255)).save(buf, "PNG")
    return buf.getvalue()


def _manifest(*, with_variants: bool = False, hero: bool = True) -> TemplateManifest:
    hero_layer = {
        "type": "hero_cutout",
        "name": "hero",
        "x": 0,
        "y": 0,
        "width": 100,
        "height": 100,
        "z": 10,
    }
    title_layer = {
        "type": "text",
        "name": "title",
        "slot": "title",
        "x": 0,
        "y": 0,
        "width": 100,
        "height": 40,
        "font_family": "SBSansDisplay",
        "font_weight": "Semibold",
        "font_size_max": 20,
        "color": "#FFFFFF",
        "z": 20,
    }
    layers = [title_layer] + ([hero_layer] if hero else [])
    scenario = {
        "title": "Test",
        "formats": ["fmt_a", "fmt_b"],
        "slots": ["title"],
    }
    if hero:
        scenario["hero"] = {"source": "both", "remove_bg": True}
    if with_variants:
        scenario["variants"] = [
            {"id": "green", "overrides": {"title": {"color": "#26D07C"}}},
            {"id": "white", "overrides": {"title": {"color": "#FFFFFF"}}},
        ]
    return TemplateManifest.model_validate(
        {
            "version": "test",
            "templates": {
                "fmt_a": {"width": 100, "height": 100, "layers": layers},
                "fmt_b": {"width": 120, "height": 80, "layers": layers},
            },
            "scenarios": {"sc": scenario},
        }
    )


@pytest.mark.asyncio
async def test_render_base(tmp_path: Path):
    files = await render_scenario(
        scenario_id="sc",
        hero=_tiny_hero(),
        texts={"title": "Привет"},
        out_dir=tmp_path,
        manifest=_manifest(),
    )
    assert len(files) == 2
    assert {f["format"] for f in files} == {"fmt_a", "fmt_b"}
    assert all(f["variant"] == "base" for f in files)
    for f in files:
        assert Path(f["path"]).exists()


@pytest.mark.asyncio
async def test_render_variants_fan_out(tmp_path: Path):
    files = await render_scenario(
        scenario_id="sc",
        hero=_tiny_hero(),
        texts={"title": "x"},
        out_dir=tmp_path,
        manifest=_manifest(with_variants=True),
    )
    # 2 formats × 2 variants
    assert len(files) == 4
    assert {f["variant"] for f in files} == {"green", "white"}


@pytest.mark.asyncio
async def test_hero_less_scenario(tmp_path: Path):
    files = await render_scenario(
        scenario_id="sc",
        hero=None,
        texts={"title": "x"},
        out_dir=tmp_path,
        manifest=_manifest(hero=False),
    )
    assert len(files) == 2


@pytest.mark.asyncio
async def test_hero_required_isolation(tmp_path: Path):
    # hero layer present but hero=None -> compose raises per format -> all
    # skipped, batch returns empty rather than blowing up.
    files = await render_scenario(
        scenario_id="sc",
        hero=None,
        texts={"title": "x"},
        out_dir=tmp_path,
        manifest=_manifest(hero=True),
    )
    assert files == []


@pytest.mark.asyncio
async def test_formats_subset(tmp_path: Path):
    files = await render_scenario(
        scenario_id="sc",
        hero=_tiny_hero(),
        texts={"title": "x"},
        out_dir=tmp_path,
        manifest=_manifest(),
        formats=["fmt_b"],
    )
    assert [f["format"] for f in files] == ["fmt_b"]


@pytest.mark.asyncio
async def test_formats_subset_rejects_unknown(tmp_path: Path):
    with pytest.raises(ValueError, match="not in scenario"):
        await render_scenario(
            scenario_id="sc",
            hero=_tiny_hero(),
            texts={"title": "x"},
            out_dir=tmp_path,
            manifest=_manifest(),
            formats=["fmt_a", "ghost"],
        )


@pytest.mark.asyncio
async def test_unknown_scenario(tmp_path: Path):
    with pytest.raises(KeyError):
        await render_scenario(
            scenario_id="nope",
            hero=_tiny_hero(),
            texts={},
            out_dir=tmp_path,
            manifest=_manifest(),
        )


@pytest.mark.asyncio
async def test_build_zip(tmp_path: Path):
    files = await render_scenario(
        scenario_id="sc",
        hero=_tiny_hero(),
        texts={"title": "x"},
        out_dir=tmp_path,
        manifest=_manifest(with_variants=True),
    )
    zip_path = await build_zip(files, tmp_path / "out.zip")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert len(names) == 4
    assert "fmt_a_green.png" in names
    assert "fmt_b_white.png" in names
