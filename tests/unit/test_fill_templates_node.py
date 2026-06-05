"""Unit tests for graph.nodes.fill_templates_per_format (M3.3 rewrite).

Covers:
- happy path: every requested slug -> rendered_files entry,
- unknown slug skipped, doesn't kill known ones,
- per-format compose error isolated (one format failure -> others still render),
- raises when state.image / state.winner missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from graph.nodes import fill_templates_per_format as mod

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_state(tmp_path: Path, formats: list[str]) -> dict:
    hero = tmp_path / "hero.png"
    Image.new("RGB", (512, 512), (200, 100, 100)).save(hero)
    return {
        "session_id": "sX",
        "brief": {
            "product": "P",
            "goal": "awareness",
            "audience_raw": "A",
            "channel": "vk_post",
            "formats": formats,
            "constraints": [],
            "age_rating": "12+",
        },
        "image": {
            "local_path": str(hero),
            "style": "stub",
            "variant": "default",
            "prompt": "",
        },
        "winner": {
            "slogan": "Тестовый слоган",
            "body": "B",
            "cta": "Купить",
            "hook_angle": "rational",
        },
    }


@pytest.mark.asyncio
async def test_node_happy_path_renders_all_known_slugs(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    state = _seed_state(tmp_path, ["banner_240x400", "banner_300x250"])
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert {r["format"] for r in out["rendered_files"]} == {
        "banner_240x400",
        "banner_300x250",
    }
    for r in out["rendered_files"]:
        img = Image.open(r["path"])
        img.verify()


@pytest.mark.asyncio
async def test_node_unknown_slug_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    state = _seed_state(tmp_path, ["banner_240x400", "totally_unknown_slug"])
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert {r["format"] for r in out["rendered_files"]} == {"banner_240x400"}


@pytest.mark.asyncio
async def test_node_compose_error_isolated_per_format(monkeypatch, tmp_path):
    """If compose() raises for one slug, the rest must still render."""
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    real_compose = mod.compose

    def _flaky_compose(spec, **kwargs):
        if spec.width == 240:
            raise RuntimeError("synthetic failure")
        return real_compose(spec, **kwargs)

    monkeypatch.setattr(mod, "compose", _flaky_compose)
    state = _seed_state(tmp_path, ["banner_240x400", "banner_300x250"])
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert {r["format"] for r in out["rendered_files"]} == {"banner_300x250"}


@pytest.mark.asyncio
async def test_node_no_image_raises(tmp_path):
    state = _seed_state(tmp_path, ["banner_240x400"])
    state["image"] = None
    with pytest.raises(ValueError, match="state.image is None"):
        await mod.fill_templates_per_format(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_node_no_winner_raises(tmp_path):
    state = _seed_state(tmp_path, ["banner_240x400"])
    state["winner"] = None
    with pytest.raises(ValueError, match="state.winner is None"):
        await mod.fill_templates_per_format(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_node_default_format_when_brief_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    state = _seed_state(tmp_path, [])  # empty formats
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert len(out["rendered_files"]) == 1
    assert out["rendered_files"][0]["format"] == mod._DEFAULT_FORMAT


@pytest.mark.asyncio
async def test_node_uses_age_rating_from_brief(monkeypatch, tmp_path):
    """The age_rating from brief must reach the rendered PNG.

    We don't OCR the PNG; we assert that the compose() call was made with
    the brief's age_rating in the texts dict.
    """
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    seen: list[dict] = []
    real_compose = mod.compose

    def _spying_compose(spec, **kwargs):
        seen.append(dict(kwargs.get("texts", {})))
        return real_compose(spec, **kwargs)

    monkeypatch.setattr(mod, "compose", _spying_compose)
    state = _seed_state(tmp_path, ["banner_300x250"])
    state["brief"]["age_rating"] = "18+"
    await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert seen[0]["age_rating"] == "18+"
    assert seen[0]["slogan"] == "Тестовый слоган"
    assert seen[0]["cta"] == "Купить"
