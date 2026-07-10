"""Block C (2026-06-21): per-candidate scenario routing + 12 image prompts.

The 12-banner redesign drops the single-hero image stage. Instead:
  - route_image_style classifies EACH of the 12 ranked propositions into a
    brand scenario (render|photo); isometric folds into render.
  - generate_image_prompt derives ONE visual METAPHOR per proposition
    (metaphor-only redesign 2026-07-10) and wraps it in our fixed composition
    clause; App1's brand enhancer owns all styling + the anti-text guard.

Both nodes keep the legacy single fields (image_style / image_prompt = the
top-ranked one) so the HITL image gate still has something to display.
"""
from __future__ import annotations

import pytest

from graph.nodes import generate_image_prompt as gip
from graph.nodes import ranked_candidates
from graph.nodes import route_image_style as ris
from graph.state import ImageMetaphorOutput, ImageStyleChoice, MessageCandidate


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


# ----- generate_image_prompt: 12 metaphors → 12 wire prompts ----------------


def _capture_users(monkeypatch) -> dict[int, str]:
    """Run generate_image_prompt over a render/photo split and return the LLM
    user-message per ranked index. The stubbed agent answers with a metaphor
    echoing the slogan so the wire prompt stays attributable."""
    seen: dict[int, str] = {}

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        user = messages[1]["content"]
        idx = next(i for i in range(11, -1, -1) if f"SLOGAN{i}" in user)
        seen[idx] = user
        return ImageMetaphorOutput(
            metaphor=f"a tangible visual metaphor for SLOGAN{idx}",
            rationale="r",
        )

    monkeypatch.setattr(gip, "run_agent", fake_run_agent)
    return seen


@pytest.mark.asyncio
async def test_build_image_prompts_one_per_candidate(monkeypatch):
    seen = _capture_users(monkeypatch)
    state = _state()
    state["scenarios"] = [
        "render" if i % 2 == 0 else "photo" for i in range(12)
    ]
    out = await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    prompts = out["image_prompts"]
    assert len(prompts) == 12
    for i, p in enumerate(prompts):
        assert f"SLOGAN{i}" in p, "wire prompt must carry the per-message metaphor"
    # legacy single field = top-ranked prompt
    assert out["image_prompt"] == prompts[0]
    assert len(seen) == 12


@pytest.mark.asyncio
async def test_llm_input_carries_message_not_scene_pool(monkeypatch):
    """The metaphor must derive from THE MESSAGE (slogan + body + hook), not
    from a canned scene pool — the LLM input carries the candidate's own
    fields, and the node no longer owns any prewritten photo scenes."""
    seen = _capture_users(monkeypatch)
    state = _state()
    state["scenarios"] = ["render"] * 6 + ["photo"] * 6
    await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    for i, user in seen.items():
        assert f"SLOGAN{i}" in user
        assert f"body{i}" in user, "candidate body must reach the LLM"
    # the 2026-06 canned photo-scene pool is gone
    assert not hasattr(gip, "_PHOTO_PEOPLE")
    assert not hasattr(gip, "_PHOTO_NO_PEOPLE")
    assert not hasattr(gip, "_photo_directives")


@pytest.mark.asyncio
async def test_render_wire_prompt_carries_our_composition(monkeypatch):
    """Positioning/composition is OURS (App1's enhancer can't invent it): the
    render wire prompt pins isometric angle, near-square frame-filling
    proportion and centered/even-margin cutout positioning."""
    _capture_users(monkeypatch)
    state = _state()
    state["scenarios"] = ["render"] * 6 + ["photo"] * 6
    out = await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    for p in out["image_prompts"][:6]:
        low = p.lower()
        assert "isometric" in low
        assert "square" in low
        assert "margin" in low
        assert "center" in low


@pytest.mark.asyncio
async def test_photo_wire_prompt_composition_no_isometric(monkeypatch):
    """Photo wire prompts carry the photo composition clause (central subject —
    the frame is cover-cropped to a tall banner) and never isometric/3D cues."""
    _capture_users(monkeypatch)
    state = _state()
    state["scenarios"] = ["render"] * 6 + ["photo"] * 6
    out = await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    for p in out["image_prompts"][6:]:
        low = p.lower()
        assert "isometric" not in low
        assert "vertical" in low, "photo clause must warn about the tall crop"


def test_composition_clauses_delegate_styling_to_app1():
    """Both composition clauses carry ONLY geometry/positioning. Styling
    (colour, materials, lighting, film look, brand green) and the anti-text
    guard are owned by App1's enhancers — prescribing them here fights the
    enhancer."""
    for clause in (gip._COMPOSITION_RENDER, gip._COMPOSITION_PHOTO):
        low = clause.lower()
        for banned in ("crystal", "emerald", "lime", "green", "metal", "glass",
                       "studio lighting", "matte", "backdrop", "kodak", "bokeh",
                       "no text", "no logos"):
            assert banned not in low, f"composition must not prescribe {banned!r}"
    low_r = gip._COMPOSITION_RENDER.lower()
    assert "square" in low_r and "fill" in low_r and "flat" in low_r
