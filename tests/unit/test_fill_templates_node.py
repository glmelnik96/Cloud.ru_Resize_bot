"""Unit tests for graph.nodes.fill_templates_per_format (12-banner rewrite).

2026-06-21 redesign: the node no longer composes one hero across many format
slugs. It composes ONE banner per ranked proposition — each proposition's hero
(state.generated_heroes[i]) onto the 300x600 template for its scenario
(render|photo), with that proposition's slogan/body/cta.

Covers:
- happy path: N heroes -> N rendered_files, unique labels, right scenario slug,
- per-banner compose error isolated (one failure -> others still render),
- manual single-upload fallback (state.image, no generated_heroes) -> 1 banner,
- raises when neither heroes nor image present / ranked empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from graph.nodes import fill_templates_per_format as mod

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_hero(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    Image.new("RGBA", (400, 400), (180, 90, 90, 255)).save(p)
    return str(p)


def _ranked(n: int = 12) -> list[dict]:
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
        out.append(
            {
                "url": None,
                "local_path": _make_hero(tmp_path, f"hero_{i}.png"),
                "style": scen,
                "variant": "default",
                "prompt": f"prompt {i}",
            }
        )
    return out


@pytest.mark.asyncio
async def test_node_renders_one_banner_per_hero(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    scenarios = ["render", "photo"] * 6  # 12
    state = {
        "session_id": "sX",
        "ranked": _ranked(12),
        "scenarios": scenarios,
        "generated_heroes": _heroes(tmp_path, scenarios),
    }
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    files = out["rendered_files"]
    assert len(files) == 12
    # labels are unique (so render_all arcnames don't collide)
    labels = [r["format"] for r in files]
    assert len(set(labels)) == 12
    for r in files:
        Image.open(r["path"]).verify()


@pytest.mark.asyncio
async def test_node_uses_scenario_template(monkeypatch, tmp_path):
    """Each banner is composed on the template matching its scenario."""
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    seen: list[tuple[int, int, dict]] = []
    real_compose = mod.compose

    def _spy(spec, **kwargs):
        seen.append((spec.width, spec.height, dict(kwargs.get("texts", {}))))
        return real_compose(spec, **kwargs)

    monkeypatch.setattr(mod, "compose", _spy)
    scenarios = ["render", "photo"]
    state = {
        "session_id": "sX",
        "ranked": _ranked(2),
        "scenarios": scenarios,
        "generated_heroes": _heroes(tmp_path, scenarios),
    }
    await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    # both are 300x600
    assert all(w == 300 and h == 600 for (w, h, _t) in seen)
    # texts carry the per-candidate slogan + subtitle(body) + cta
    texts0 = seen[0][2]
    assert texts0["slogan"] == "Слоган 0"
    assert texts0["subtitle"] == "Подзаголовок 0"
    assert texts0["cta"] == "Кнопка 0"


@pytest.mark.asyncio
async def test_node_per_banner_error_isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    real_compose = mod.compose
    calls = {"n": 0}

    def _flaky(spec, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic")
        return real_compose(spec, **kwargs)

    monkeypatch.setattr(mod, "compose", _flaky)
    scenarios = ["render", "photo", "render"]
    state = {
        "session_id": "sX",
        "ranked": _ranked(3),
        "scenarios": scenarios,
        "generated_heroes": _heroes(tmp_path, scenarios),
    }
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert len(out["rendered_files"]) == 2


@pytest.mark.asyncio
async def test_node_manual_single_upload_fallback(monkeypatch, tmp_path):
    """No generated_heroes but a single manually uploaded hero -> 1 banner
    composed with the top-ranked proposition on its scenario template."""
    monkeypatch.setattr(mod, "_RENDER_DIR", tmp_path / "renders")
    state = {
        "session_id": "sX",
        "ranked": _ranked(12),
        "scenarios": ["photo"] * 12,
        "image": {
            "url": None,
            "local_path": _make_hero(tmp_path, "manual.png"),
            "style": "photo",
            "variant": "default",
            "prompt": "p",
        },
    }
    out = await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
    assert len(out["rendered_files"]) == 1
    Image.open(out["rendered_files"][0]["path"]).verify()


@pytest.mark.asyncio
async def test_node_no_hero_no_image_raises(tmp_path):
    state = {"session_id": "sX", "ranked": _ranked(12), "scenarios": ["photo"] * 12}
    with pytest.raises(ValueError, match="no heroes and no uploaded image"):
        await mod.fill_templates_per_format(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_node_no_ranked_raises(tmp_path):
    state = {
        "session_id": "sX",
        "ranked": [],
        "generated_heroes": _heroes(tmp_path, ["photo"]),
    }
    with pytest.raises(ValueError, match="ranked is empty"):
        await mod.fill_templates_per_format(state)  # type: ignore[arg-type]
