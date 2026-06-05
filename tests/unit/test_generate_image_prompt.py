"""Unit tests for graph.nodes.generate_image_prompt (M3.3).

We stub run_agent — the node's behavior we care about is:
- state shape: brief / personas / persona_priority / winner / image_style → user_msg,
- bad / missing image_style falls back to 'photo',
- soft validators only warn (don't raise) on length / cyrillic / no-text-guard,
- returns {"image_prompt": prompt}.
"""

from __future__ import annotations

from typing import Any

import pytest

from graph.nodes import generate_image_prompt as mod
from graph.state import ImagePromptOutput


def _good_state(**overrides: Any) -> dict:
    state = {
        "session_id": "s1",
        "brief": {
            "product": "Cloud.ru Evolution",
            "goal": "consideration",
            "audience_raw": "DevOps в финтехе",
            "channel": "tg_post",
            "formats": ["banner_300x250"],
            "tone_hints": "уверенный",
            "constraints": [],
            "age_rating": "0+",
        },
        "personas": [
            {
                "segment": "DevOps mid-market",
                "age_range": "28-40",
                "pain_points": ["downtime", "cost"],
                "motivations": ["надёжность"],
                "objections": ["миграция дорого"],
                "communication_style": "рациональный",
            }
        ],
        "persona_priority": 0,
        "winner": {
            "slogan": "Инфра без сюрпризов",
            "body": "B",
            "cta": "Подробнее",
            "hook_angle": "rational",
        },
        "image_style": "render",
    }
    state.update(overrides)
    return state


@pytest.fixture
def _stub_agent(monkeypatch):
    """Stub run_agent to return whatever ImagePromptOutput the test wants."""
    calls: list[dict] = []

    def _make_stub(result: ImagePromptOutput):
        async def _stub(agent_id, *, messages, schema, session_id=None):
            calls.append(
                {
                    "agent_id": agent_id,
                    "schema": schema,
                    "messages": messages,
                    "session_id": session_id,
                }
            )
            return result

        monkeypatch.setattr(mod, "run_agent", _stub)
        return calls

    return _make_stub


@pytest.mark.asyncio
async def test_happy_path_returns_prompt(_stub_agent):
    good = ImagePromptOutput(
        prompt=(
            "Studio render of a sleek server rack on a matte concrete floor, "
            "soft side lighting, subtle lemon green accent cable, right-biased "
            "composition, ample clean negative space on the left third, calm "
            "engineering atmosphere, no text, no letters, no logos."
        ),
        rationale="Calm rational render fits DevOps mid-market.",
    )
    _stub_agent(good)
    out = await mod.generate_image_prompt(_good_state())  # type: ignore[arg-type]
    assert "image_prompt" in out
    assert out["image_prompt"].startswith("Studio render")


@pytest.mark.asyncio
async def test_unknown_image_style_falls_back_to_photo(_stub_agent, caplog):
    good = ImagePromptOutput(
        prompt="A documentary photo of a small team in a sunlit office, " * 4
        + "no text, no letters, no logos.",
        rationale="ok",
    )
    calls = _stub_agent(good)
    await mod.generate_image_prompt(  # type: ignore[arg-type]
        _good_state(image_style="cubism")
    )
    user_msg = calls[0]["messages"][1]["content"]
    assert "CHOSEN VISUAL STYLE: photo" in user_msg


@pytest.mark.asyncio
async def test_missing_image_style_falls_back_to_photo(_stub_agent):
    good = ImagePromptOutput(
        prompt="A documentary photo of a small team in a sunlit office, " * 4
        + "no text, no letters, no logos.",
        rationale="ok",
    )
    calls = _stub_agent(good)
    state = _good_state()
    state.pop("image_style")
    await mod.generate_image_prompt(state)  # type: ignore[arg-type]
    assert "CHOSEN VISUAL STYLE: photo" in calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_empty_personas_raises(_stub_agent):
    _stub_agent(
        ImagePromptOutput(prompt="x" * 25, rationale="r")
    )
    state = _good_state(personas=[])
    with pytest.raises(ValueError, match="personas is empty"):
        await mod.generate_image_prompt(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_winner_raises(_stub_agent):
    _stub_agent(ImagePromptOutput(prompt="x" * 25, rationale="r"))
    state = _good_state()
    state["winner"] = None
    with pytest.raises(ValueError, match="winner is None"):
        await mod.generate_image_prompt(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persona_priority_clamped_to_available(_stub_agent):
    """persona_priority=5 with one persona should not IndexError."""
    good = ImagePromptOutput(
        prompt="A documentary photo of a small team in a sunlit office, " * 4
        + "no text, no letters, no logos.",
        rationale="ok",
    )
    calls = _stub_agent(good)
    await mod.generate_image_prompt(  # type: ignore[arg-type]
        _good_state(persona_priority=5)
    )
    user_msg = calls[0]["messages"][1]["content"]
    assert "DevOps mid-market" in user_msg


@pytest.mark.asyncio
async def test_soft_validator_does_not_raise_on_bad_prompt(_stub_agent):
    """Short, cyrillic, no-text-guard-missing prompt must still return."""
    bad = ImagePromptOutput(
        prompt="Короткий русский промпт без защиты от текста абсолютно.",
        rationale="r",
    )
    _stub_agent(bad)
    out = await mod.generate_image_prompt(_good_state())  # type: ignore[arg-type]
    # Did not raise. The node returns the prompt as-is.
    assert "image_prompt" in out


def test_word_count_helper():
    assert mod._word_count("") == 0
    assert mod._word_count("one two three") == 3
    assert mod._word_count("   spaced   words  here ") == 3


def test_soft_validate_does_not_raise():
    # Empty / minimal / pathological inputs — all warn-only.
    mod._soft_validate("", session_id=None)
    mod._soft_validate("a b c", session_id=None)
    mod._soft_validate("Тест" * 50, session_id="s")
