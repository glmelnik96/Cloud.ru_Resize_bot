"""Unit tests for the select_by_persona node (the ЦА critic).

Replaces test_rank_candidates: that node could only reorder the 12 drafts that
were shipping anyway. Here the persona keeps 12 of 24 — so the merge has to be
robust to the model naming an id that is not in the pool, naming the same id
twice, or otherwise returning fewer usable picks than the banner stage needs.

Covered:
- picks are merged with the candidate objects and sorted best-first,
- exactly KEEP items come out even when the model repeats/invents ids, and the
  shortfall is topped up from the unpicked drafts with score 0.
"""

from __future__ import annotations

from types import SimpleNamespace

from graph.nodes import select_by_persona as mod
from graph.state import MessageCandidate, Persona, SelectedItem, SelectionSet


def _cand(cid: str) -> MessageCandidate:
    return MessageCandidate(
        id=cid,
        slogan=f"S-{cid}",
        body=f"body-{cid}",
        cta="cta",
        hook_angle="rational",
    )


def _pool(n: int) -> list[MessageCandidate]:
    return [_cand(f"c{i}") for i in range(1, n + 1)]


def _persona() -> Persona:
    return Persona(
        segment="seg",
        age_range="25-35",
        pain_points=["p"],
        motivations=["m"],
        objections=["o"],
        communication_style="прямой",
    )


def _state(candidates: list[MessageCandidate]) -> dict:
    return {
        "session_id": "s-test",
        "brief": {
            "product": "Cloud.ru Evolution",
            "audience_raw": "DevOps в финтехе",
            "emotion": "спокойная уверенность",
        },
        "product": {
            "canonical_name": "Evolution Managed RAG",
            "what_it_is": "Сервис поиска ответов по корпоративным документам.",
            "key_capabilities": ["индексация документов", "поиск по смыслу"],
            "problems_solved": ["ответы теряются в чатах", "долгий онбординг"],
        },
        "candidates": [c.model_dump() for c in candidates],
        "personas": [_persona().model_dump()],
    }


def _patch_skill(monkeypatch, selection: SelectionSet) -> None:
    monkeypatch.setattr(mod, "load_skill", lambda name: SimpleNamespace(body=""))
    monkeypatch.setattr(mod, "extract_section", lambda body, header: "tpl")
    monkeypatch.setattr(mod, "render", lambda tpl, **kw: "rendered")

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return selection

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)


async def test_selection_sorted_best_first_and_merged(monkeypatch):
    cands = _pool(24)
    selection = SelectionSet.model_construct(
        selected=[
            SelectedItem(candidate_id=f"c{i}", score=i / 2, reason=f"r{i}")
            for i in range(1, 13)
        ]
    )
    _patch_skill(monkeypatch, selection)

    ranked = (await mod.select_by_persona(_state(cands)))["ranked"]

    assert len(ranked) == mod.KEEP
    assert ranked[0]["id"] == "c12"  # highest score first
    assert ranked[0]["score"] == 6.0
    assert ranked[0]["reason"] == "r12"
    assert ranked[0]["slogan"] == "S-c12"


async def test_duplicate_and_unknown_ids_are_topped_up(monkeypatch):
    cands = _pool(24)
    selection = SelectionSet.model_construct(
        selected=[
            SelectedItem(candidate_id="c1", score=9.0, reason="ok"),
            SelectedItem(candidate_id="c1", score=8.0, reason="дубль"),
            SelectedItem(candidate_id="c999", score=7.0, reason="такого нет"),
        ]
    )
    _patch_skill(monkeypatch, selection)

    ranked = (await mod.select_by_persona(_state(cands)))["ranked"]
    ids = [r["id"] for r in ranked]

    assert len(ranked) == mod.KEEP
    assert len(set(ids)) == mod.KEEP  # no duplicates
    assert "c999" not in ids
    assert ids[0] == "c1"
    assert all(r["score"] == 0.0 for r in ranked[1:])  # top-ups
