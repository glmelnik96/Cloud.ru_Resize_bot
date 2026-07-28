"""Unit tests for the two-batch generator.

The node's whole point is that the two calls do NOT converge — otherwise 24
drafts are 12 drafts plus 12 paraphrases and the critic downstream has nothing
real to choose between. So what is pinned here is that the batches actually
receive different creative stances and different anchor slices, and that the
merged pool keeps 24 distinct candidates.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph.nodes import generate_message_candidates as mod
from graph.state import CandidateSet, DraftCandidate, Persona


def _persona() -> Persona:
    return Persona(
        segment="техлид",
        age_range="30-45",
        pain_points=["боль1", "боль2"],
        motivations=["мотив1", "мотив2"],
        objections=["возражение1", "возражение2"],
        communication_style="прямой",
    )


def _state() -> dict:
    return {
        "session_id": "s-test",
        "brief": {
            "product": "Evolution Managed RAG",
            "audience_raw": "техлиды",
            "emotion": "спокойная уверенность",
        },
        "product": {
            "canonical_name": "Evolution Managed RAG",
            "what_it_is": "Управляемый сервис поиска по документам компании.",
            "key_capabilities": ["индексация", "поиск по смыслу"],
            "problems_solved": ["ответы теряются", "долгий онбординг"],
        },
        "personas": [_persona().model_dump()],
    }


def _batch(tag: str) -> CandidateSet:
    return CandidateSet.model_construct(
        candidates=[
            DraftCandidate(
                slogan=f"{tag}-{i}",
                body=f"body-{tag}-{i}",
                cta="Попробовать",
                hook_angle="rational",
            )
            for i in range(12)
        ]
    )


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    """Stub prompt loading + the LLM; record what each batch was rendered with."""
    seen: list[dict] = []
    monkeypatch.setattr(mod, "load_skill", lambda name: SimpleNamespace(body=""))
    monkeypatch.setattr(mod, "extract_section", lambda body, header: header)

    def fake_render(tpl, **kw):
        seen.append(kw)
        return "rendered"

    monkeypatch.setattr(mod, "render", fake_render)

    n = {"i": 0}

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        n["i"] += 1
        return _batch(f"b{n['i']}")

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)
    return seen


async def test_produces_24_distinct_candidates(calls):
    out = await mod.generate_message_candidates(_state())
    candidates = out["candidates"]

    assert len(candidates) == 24
    assert len({c["id"] for c in candidates}) == 24, "ids collided between batches"


async def test_batches_get_different_stances(calls):
    await mod.generate_message_candidates(_state())

    directives = [kw["batch_directive"] for kw in calls if "batch_directive" in kw]
    assert len(directives) == 2
    assert directives[0] != directives[1]
    assert set(directives) == set(mod._BATCH_DIRECTIVES)


async def test_batches_get_different_anchor_slices(calls):
    await mod.generate_message_candidates(_state())

    slices = [kw["batch_anchors"] for kw in calls if "batch_anchors" in kw]
    assert len(slices) == 2
    assert slices[0] != slices[1]


def test_anchor_slices_are_dealt_alternately_not_split():
    a, b = mod._anchor_slices(_persona())
    # each slice must be mixed across pains/motivations/objections
    for slice_ in (a, b):
        kinds = {item.split(":", 1)[0] for item in slice_}
        assert kinds == {"боль", "мотивация", "возражение"}
    assert len(a) + len(b) == 6
    assert not set(a) & set(b)


async def test_empty_personas_is_an_error(calls):
    state = _state()
    state["personas"] = []
    with pytest.raises(ValueError, match="state.personas is empty"):
        await mod.generate_message_candidates(state)
