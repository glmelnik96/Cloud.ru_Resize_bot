"""Unit tests for graph.nodes.generate_image_prompt (metaphor-only, 2026-07-10).

We stub run_agent — the node's behavior we care about is:
- state shape: brief / personas / ranked / scenarios → one LLM call per
  proposition, the LLM returns ONLY a visual metaphor,
- the wire prompt = metaphor + OUR fixed composition clause (per scenario),
- scenarios shorter than ranked (or missing) fall back to 'photo',
- soft validators only warn (don't raise) on length / cyrillic,
- returns {"image_prompts": [...], "image_prompt": prompts[0]}.

App1's brand enhancers own ALL styling + the anti-text guard; we no longer
soft-check for "no text" in the prompt.
"""

from __future__ import annotations

from typing import Any

import pytest

from graph.nodes import generate_image_prompt as mod
from graph.state import ImageMetaphorOutput


def _good_state(**overrides: Any) -> dict:
    state = {
        "session_id": "s1",
        "brief": {
            "product": "Cloud.ru Evolution",
            "goal": "consideration",
            "audience_raw": "DevOps в финтехе",
            "channel": "tg_post",
            "formats": ["banner_300x600_render"],
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
        "ranked": [
            {
                "id": "c1",
                "slogan": "Инфра без сюрпризов",
                "body": "Бьём в боль простоев: кластер сам чинит себя.",
                "cta": "Подробнее",
                "hook_angle": "rational",
                "score": 9.0,
                "reason": "топ",
            }
        ],
        "scenarios": ["render"],
    }
    state.update(overrides)
    return state


@pytest.fixture
def _stub_agent(monkeypatch):
    """Stub run_agent to return whatever ImageMetaphorOutput the test wants."""
    calls: list[dict] = []

    def _make_stub(result: ImageMetaphorOutput):
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


_METAPHOR = ImageMetaphorOutput(
    metaphor="a self-repairing engine block that reassembles itself",
    rationale="tangible uptime metaphor",
)


@pytest.mark.asyncio
async def test_happy_path_wire_prompt_is_metaphor_plus_composition(_stub_agent):
    _stub_agent(_METAPHOR)
    out = await mod.generate_image_prompt(_good_state())  # type: ignore[arg-type]
    assert out["image_prompts"] == [out["image_prompt"]]
    p = out["image_prompt"]
    assert p.startswith("a self-repairing engine block")
    # render scenario → OUR render composition clause appended
    assert "isometric" in p.lower()


@pytest.mark.asyncio
async def test_llm_receives_message_fields_and_schema(_stub_agent):
    calls = _stub_agent(_METAPHOR)
    await mod.generate_image_prompt(_good_state())  # type: ignore[arg-type]
    user_msg = calls[0]["messages"][1]["content"]
    assert "Инфра без сюрпризов" in user_msg
    assert "кластер сам чинит себя" in user_msg  # body reaches the LLM
    assert calls[0]["schema"] is ImageMetaphorOutput


@pytest.mark.asyncio
async def test_unknown_scenario_falls_back_to_photo(_stub_agent):
    _stub_agent(_METAPHOR)
    out = await mod.generate_image_prompt(  # type: ignore[arg-type]
        _good_state(scenarios=["cubism"])
    )
    low = out["image_prompt"].lower()
    assert "isometric" not in low
    assert "vertical" in low  # photo composition clause


@pytest.mark.asyncio
async def test_missing_scenarios_fall_back_to_photo(_stub_agent):
    _stub_agent(_METAPHOR)
    state = _good_state()
    state.pop("scenarios")
    out = await mod.generate_image_prompt(state)  # type: ignore[arg-type]
    assert "isometric" not in out["image_prompt"].lower()


@pytest.mark.asyncio
async def test_empty_personas_raises(_stub_agent):
    _stub_agent(_METAPHOR)
    state = _good_state(personas=[])
    with pytest.raises(ValueError, match="personas is empty"):
        await mod.generate_image_prompt(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_empty_ranked_raises(_stub_agent):
    _stub_agent(_METAPHOR)
    state = _good_state()
    state["ranked"] = []
    with pytest.raises(ValueError, match="ranked is empty"):
        await mod.generate_image_prompt(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_uses_single_persona(_stub_agent):
    calls = _stub_agent(_METAPHOR)
    await mod.generate_image_prompt(_good_state())  # type: ignore[arg-type]
    user_msg = calls[0]["messages"][1]["content"]
    assert "DevOps mid-market" in user_msg


@pytest.mark.asyncio
async def test_soft_validator_does_not_raise_on_bad_metaphor(_stub_agent):
    """Cyrillic / out-of-band metaphor must still return (warn-only)."""
    bad = ImageMetaphorOutput(
        metaphor="Короткая русская метафора",
        rationale="r",
    )
    _stub_agent(bad)
    out = await mod.generate_image_prompt(_good_state())  # type: ignore[arg-type]
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
