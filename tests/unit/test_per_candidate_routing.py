"""Block C (2026-06-21): per-candidate scenario routing + 12 image prompts.

The 12-banner redesign drops the single-hero image stage. Instead:
  - route_image_style classifies EACH of the 12 ranked propositions into a
    brand scenario (render|photo); isometric folds into render.
  - generate_image_prompt writes ONE EN prompt per proposition, using that
    proposition's scenario as the visual-style cue.

Both nodes keep the legacy single fields (image_style / image_prompt = the
top-ranked one) so the HITL image gate still has something to display.
"""
from __future__ import annotations

import pytest

from graph.nodes import generate_image_prompt as gip
from graph.nodes import ranked_candidates
from graph.nodes import route_image_style as ris
from graph.state import ImagePromptOutput, ImageStyleChoice, MessageCandidate


def _brief() -> dict:
    return {
        "product": "Cloud.ru",
        "goal": "awareness",
        "audience_raw": "devops",
        "channel": "vk_ad",
        "formats": ["banner_300x600_render"],
        "constraints": [],
        "age_rating": "0+",
    }


def _persona() -> dict:
    return {
        "segment": "devops",
        "age_range": "25-40",
        "pain_points": ["downtime"],
        "motivations": ["speed"],
        "objections": ["cost"],
        "communication_style": "tech",
    }


def _ranked(n: int = 12) -> list[dict]:
    return [
        {
            "id": f"c{i}",
            "slogan": f"SLOGAN{i}",
            "body": f"body{i}",
            "cta": f"CTA{i}",
            "hook_angle": "rational",
            "score": float(12 - i),
            "reason": "r",
        }
        for i in range(n)
    ]


def _state() -> dict:
    return {
        "session_id": "sX",
        "brief": _brief(),
        "personas": [_persona()],
        "ranked": _ranked(),
    }


# ----- ranked_candidates helper --------------------------------------------


def test_ranked_candidates_returns_all_in_order():
    out = ranked_candidates(_state())  # type: ignore[arg-type]
    assert len(out) == 12
    assert all(isinstance(c, MessageCandidate) for c in out)
    assert [c.slogan for c in out] == [f"SLOGAN{i}" for i in range(12)]


def test_ranked_candidates_empty_raises():
    with pytest.raises(ValueError, match="ranked is empty"):
        ranked_candidates({"ranked": []})  # type: ignore[arg-type]


# ----- route_image_style: per-candidate scenarios --------------------------


@pytest.mark.asyncio
async def test_route_scenarios_one_per_candidate(monkeypatch):
    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        user = messages[1]["content"]
        # even-indexed slogans -> isometric (folds to render), odd -> photo
        idx = next(i for i in range(11, -1, -1) if f"SLOGAN{i}" in user)
        style = "isometric" if idx % 2 == 0 else "photo"
        return ImageStyleChoice(style=style, rationale="x")

    monkeypatch.setattr(ris, "run_agent", fake_run_agent)
    out = await ris.route_image_style(_state())  # type: ignore[arg-type]
    scenarios = out["scenarios"]
    assert len(scenarios) == 12
    assert set(scenarios) <= {"render", "photo"}
    assert scenarios == [
        "render" if i % 2 == 0 else "photo" for i in range(12)
    ]
    # legacy single field = the top-ranked scenario
    assert out["image_style"] == scenarios[0] == "render"


@pytest.mark.asyncio
async def test_route_all_unknown_forced_to_even_split(monkeypatch):
    """Unknown verdicts collapse to photo, but the strict even split then forces
    exactly half render: the best-ranked propositions become render."""
    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return ImageStyleChoice(style="weird-value", rationale="x")

    monkeypatch.setattr(ris, "run_agent", fake_run_agent)
    out = await ris.route_image_style(_state())  # type: ignore[arg-type]
    scenarios = out["scenarios"]
    assert scenarios.count("render") == 6
    assert scenarios.count("photo") == 6


@pytest.mark.asyncio
async def test_route_locks_even_split_when_classifier_all_render(monkeypatch):
    """Strict 6/6 lock: an all-render classifier is trimmed to exactly 6 render.
    The best-ranked 6 stay render (flips happen from the worst-ranked tail)."""
    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return ImageStyleChoice(style="render", rationale="x")

    monkeypatch.setattr(ris, "run_agent", fake_run_agent)
    out = await ris.route_image_style(_state())  # type: ignore[arg-type]
    scenarios = out["scenarios"]
    assert scenarios.count("render") == 6
    assert scenarios.count("photo") == 6
    # best-ranked 6 kept render, worst-ranked 6 flipped to photo
    assert scenarios == ["render"] * 6 + ["photo"] * 6
    assert out["image_style"] == "render"  # top-ranked classifier pick preserved


@pytest.mark.asyncio
async def test_route_locks_even_split_when_classifier_all_photo(monkeypatch):
    """Strict 6/6 lock: an all-photo classifier is topped up to exactly 6 render.
    The best-ranked propositions are promoted to render."""
    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return ImageStyleChoice(style="photo", rationale="x")

    monkeypatch.setattr(ris, "run_agent", fake_run_agent)
    out = await ris.route_image_style(_state())  # type: ignore[arg-type]
    scenarios = out["scenarios"]
    assert scenarios.count("render") == 6
    assert scenarios.count("photo") == 6
    assert scenarios == ["render"] * 6 + ["photo"] * 6


@pytest.mark.asyncio
async def test_route_keeps_balanced_split_verbatim(monkeypatch):
    """When the classifier already yields exactly 6/6, the split is untouched —
    each proposition keeps its own verdict."""
    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        user = messages[1]["content"]
        idx = next(i for i in range(11, -1, -1) if f"SLOGAN{i}" in user)
        style = "photo" if idx % 2 == 0 else "render"
        return ImageStyleChoice(style=style, rationale="x")

    monkeypatch.setattr(ris, "run_agent", fake_run_agent)
    out = await ris.route_image_style(_state())  # type: ignore[arg-type]
    assert out["scenarios"] == [
        "photo" if i % 2 == 0 else "render" for i in range(12)
    ]
    assert out["scenarios"].count("render") == 6


# ----- generate_image_prompt: 12 prompts -----------------------------------


@pytest.mark.asyncio
async def test_build_image_prompts_one_per_candidate(monkeypatch):
    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        user = messages[1]["content"]
        idx = next(i for i in range(11, -1, -1) if f"SLOGAN{i}" in user)
        # echo the image_style cue so we can assert scenario routing
        style = "render" if "render" in user else "photo"
        return ImagePromptOutput(
            prompt=f"prompt for SLOGAN{idx} as {style}, no text",
            rationale="r",
        )

    monkeypatch.setattr(gip, "run_agent", fake_run_agent)
    state = _state()
    state["scenarios"] = [
        "render" if i % 2 == 0 else "photo" for i in range(12)
    ]
    out = await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    prompts = out["image_prompts"]
    assert len(prompts) == 12
    for i, p in enumerate(prompts):
        assert f"SLOGAN{i}" in p
        expected_style = "render" if i % 2 == 0 else "photo"
        assert expected_style in p
    # legacy single field = top-ranked prompt
    assert out["image_prompt"] == prompts[0]


@pytest.mark.asyncio
async def test_render_scenario_bakes_isometric_into_prompt_input(monkeypatch):
    """render must carry an explicit isometric viewpoint into the LLM input;
    photo must NOT (App1 render = 3D product shot, we want it isometric)."""
    seen: dict[int, str] = {}

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        user = messages[1]["content"]
        idx = next(i for i in range(11, -1, -1) if f"SLOGAN{i}" in user)
        seen[idx] = user
        return ImagePromptOutput(
            prompt=f"prompt number {idx} for the hero, no text", rationale="r"
        )

    monkeypatch.setattr(gip, "run_agent", fake_run_agent)
    state = _state()
    state["scenarios"] = [
        "render" if i % 2 == 0 else "photo" for i in range(12)
    ]
    await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    for i, user in seen.items():
        if i % 2 == 0:  # render
            assert "isometric" in user.lower()
        else:  # photo
            assert "isometric" not in user.lower()
