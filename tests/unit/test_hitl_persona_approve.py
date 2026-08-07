"""hitl_persona_approve: пауза «Кому пишем» — правка персоны без LLM-вызова."""

from __future__ import annotations

import pytest

from graph.nodes import hitl_persona_approve as mod

_PERSONA = {
    "segment": "ML-инженер в продуктовой команде",
    "age_range": "28-40",
    "pain_points": ["очередь на GPU"],
    "motivations": ["запускать модели за минуты"],
    "objections": ["не смогу поставить свои библиотеки"],
    "communication_style": "инженерный, без пафоса",
}


def _state(**over):
    base = {
        "session_id": "s1",
        "personas": [_PERSONA],
        "kb_match": {"slug": "evolution-ml-inference", "name": "X", "version": 1},
    }
    base.update(over)
    return base


@pytest.fixture
def decide(monkeypatch):
    def _arm(decision):
        seen: dict = {}

        def fake_interrupt(payload):
            seen["payload"] = payload
            return decision
        monkeypatch.setattr(mod, "interrupt", fake_interrupt)
        return seen
    return _arm


async def test_payload_carries_persona_and_kb(decide):
    seen = decide({"action": "approve"})
    out = await mod.hitl_persona_approve(_state())
    assert seen["payload"]["kind"] == "persona_approve"
    assert seen["payload"]["persona"] == _PERSONA
    assert seen["payload"]["kb_match"]["slug"] == "evolution-ml-inference"
    assert out == {"persona_approved": True}


async def test_approve_with_edited_persona_replaces_state(decide):
    edited = dict(_PERSONA, pain_points=["правленая боль"])
    decide({"action": "approve", "persona": edited})
    out = await mod.hitl_persona_approve(_state())
    assert out["persona_approved"] is True
    assert out["personas"][0]["pain_points"] == ["правленая боль"]


async def test_approve_with_invalid_persona_is_fail_open(decide):
    """API валидирует раньше; узел на мусор не падает, а берёт оригинал."""
    decide({"action": "approve", "persona": {"segment": ""}})
    out = await mod.hitl_persona_approve(_state())
    assert out == {"persona_approved": True}


async def test_regenerate_clears_personas(decide):
    decide({"action": "regenerate"})
    out = await mod.hitl_persona_approve(_state())
    assert out == {"personas": [], "persona_approved": False}


async def test_timeout_and_cancel(decide):
    decide({"action": "timeout"})
    out = await mod.hitl_persona_approve(_state())
    assert out["cancelled"] is True and out["error"] == "persona_approve_timeout"
    decide({"action": "cancel"})
    out = await mod.hitl_persona_approve(_state())
    assert out == {"cancelled": True}
