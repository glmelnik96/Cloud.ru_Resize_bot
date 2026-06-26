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


def _capture_users(monkeypatch) -> dict[int, str]:
    """Run generate_image_prompt over a render/photo split and return the LLM
    user-message per ranked index."""
    seen: dict[int, str] = {}

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        user = messages[1]["content"]
        idx = next(i for i in range(11, -1, -1) if f"SLOGAN{i}" in user)
        seen[idx] = user
        return ImagePromptOutput(
            prompt=f"hero prompt number {idx} for the banner, no text",
            rationale="r",
        )

    monkeypatch.setattr(gip, "run_agent", fake_run_agent)
    return seen


@pytest.mark.asyncio
async def test_photo_directives_are_distinct_per_banner(monkeypatch):
    """All photo banners must NOT share one identical visual directive — each
    photo carries its own varied scene cue (demographics / framing / setting)."""
    seen = _capture_users(monkeypatch)
    state = _state()
    # 6 render + 6 photo (the production 6/6 lock).
    state["scenarios"] = ["render"] * 6 + ["photo"] * 6
    await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    photo_users = [seen[i] for i in range(6, 12)]
    # The injected CHOSEN VISUAL STYLE line must differ across photos.
    style_lines = [
        next(ln for ln in u.splitlines() if "CHOSEN VISUAL STYLE" in ln)
        for u in photo_users
    ]
    assert len(set(style_lines)) == len(style_lines), "photo directives repeat"


@pytest.mark.asyncio
async def test_one_third_of_photos_have_no_people(monkeypatch):
    """~1/3 of the photo banners must be people-free scenes (objects / spaces),
    the rest feature a real person. With 6 photos that is exactly 2 no-people."""
    seen = _capture_users(monkeypatch)
    state = _state()
    state["scenarios"] = ["render"] * 6 + ["photo"] * 6
    await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    photo_users = [seen[i] for i in range(6, 12)]
    no_people = [u for u in photo_users if "no people in the scene" in u]
    assert len(no_people) == 2, f"expected 2 no-people photos, got {len(no_people)}"
    # render banners are never tagged with the photo no-people marker
    render_users = [seen[i] for i in range(6)]
    assert not any("no people in the scene" in u for u in render_users)


def test_render_directive_delegates_styling_to_app1():
    """The render directive must carry ONLY the metaphor 'device', the isometric
    angle and the positioning for a clean cutout. Materials, colour (the brand
    green accent), finish and lighting are added by App1's render enhancer, so
    the directive must NOT prescribe them — over-specifying them here fights the
    enhancer (e.g. the forced 'big green crystal' kept rendering blue/clear)."""
    low = gip._STYLE_DIRECTIVE_RENDER.lower()
    # keeps: isometric angle + positioning (cutout pins)
    assert "isometric" in low
    assert "center" in low and "margin" in low
    # drops: brand styling the App1 enhancer owns
    for banned in ("crystal", "emerald", "lime", "green", "metal", "glass",
                   "studio lighting", "matte", "backdrop"):
        assert banned not in low, f"render directive must not prescribe {banned!r}"


def test_render_directive_pins_near_square_filling_proportion():
    """The composer alpha-bbox-crops then contain-fits the cutout into a
    full-width band, so the device's *aspect ratio* — not its source size —
    decides how large it reads. A wide-flat or tall-thin object letterboxes
    small; only a roughly-square, frame-filling object fills the band. The
    directive must therefore pin a compact ~square proportion that fills the
    composition (this is the size/placement fix App1's enhancer can't supply)."""
    low = gip._STYLE_DIRECTIVE_RENDER.lower()
    assert "square" in low, "render directive must pin a near-square proportion"
    assert "fill" in low, "render directive must ask the object to fill the frame"
    assert "flat" in low, "render directive must warn against wide-and-flat objects"


def test_photo_directives_delegate_styling_to_app1():
    """Photo directives must carry ONLY subject + action (the mood metaphor),
    framing intent and the people/no-people marker. Palette, film stock, lens,
    depth of field, lighting and the green accent are added by App1's photo
    enhancer — the directive must not duplicate them."""
    for d in gip._PHOTO_PEOPLE + gip._PHOTO_NO_PEOPLE:
        low = d.lower()
        for banned in ("kodak", "portra 400", "50mm", "35mm", "85mm", "f/1.8",
                       "depth of field", "ambient light", "window light",
                       "directional light", "bokeh", "grain", "#25d07b"):
            assert banned not in low, f"photo directive must not prescribe {banned!r}: {d}"
    # the people-free variants still carry the literal cutting marker
    assert all("no people in the scene" in d for d in gip._PHOTO_NO_PEOPLE)


@pytest.mark.asyncio
async def test_render_directive_fixes_object_positioning(monkeypatch):
    """The render directive sent to App1 must pin object positioning (fully
    visible, centered, even margins) so cutouts come back consistently framed."""
    seen = _capture_users(monkeypatch)
    state = _state()
    state["scenarios"] = ["render"] * 6 + ["photo"] * 6
    await gip.generate_image_prompt(state)  # type: ignore[arg-type]
    for i in range(6):
        low = seen[i].lower()
        assert "centered" in low or "center" in low
        assert "margin" in low
