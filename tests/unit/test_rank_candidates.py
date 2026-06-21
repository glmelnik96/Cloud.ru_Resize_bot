"""Unit tests for the rank_candidates node (single light ranker).

Replaces the heavy evaluate_as_persona_loop (12×N verdicts + winner/revise).
The ranker makes ONE LLM call that orders the 12 candidates by predicted
resonance and attaches a one-line "почему зайдёт ЦА" per card. There is no
winner — the whole ordered set is handed to HITL.

Covered:
- node merges the LLM ranking with the candidate objects, sorts best-first,
  and emits state["ranked"] as candidate dict + score + reason,
- a candidate the LLM omitted from the ranking is still included (appended).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from graph.nodes import rank_candidates as mod
from graph.state import MessageCandidate, Persona, RankingItem, RankingSet


def _cand(cid: str, slogan: str) -> MessageCandidate:
    return MessageCandidate(
        id=cid,
        slogan=slogan,
        body=f"body-{cid}",
        cta="cta",
        hook_angle="rational",
    )


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
            "goal": "consideration",
            "audience_raw": "DevOps в финтехе",
            "emotion": "спокойная уверенность",
            "channel": "tg_post",
            "formats": ["banner_300x250"],
            "constraints": [],
            "age_rating": "0+",
        },
        "candidates": [c.model_dump() for c in candidates],
        "personas": [_persona().model_dump()],
    }


def _patch_skill(monkeypatch) -> None:
    monkeypatch.setattr(mod, "load_skill", lambda name: SimpleNamespace(body=""))
    monkeypatch.setattr(mod, "_extract_section", lambda body, header: "tpl")
    monkeypatch.setattr(mod, "_render", lambda tpl, **kw: "rendered")


async def test_rank_orders_and_merges(monkeypatch):
    cands = [_cand("c1", "S1"), _cand("c2", "S2"), _cand("c3", "S3")]
    ranking = RankingSet.model_construct(
        ranking=[
            RankingItem(candidate_id="c2", score=9.0, reason="бьёт в главную боль"),
            RankingItem(candidate_id="c1", score=5.0, reason="общий угол"),
            RankingItem(candidate_id="c3", score=7.0, reason="хорошая мотивация"),
        ]
    )
    _patch_skill(monkeypatch)

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return ranking

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)

    out = await mod.rank_candidates(_state(cands))
    ranked = out["ranked"]

    assert [r["id"] for r in ranked] == ["c2", "c3", "c1"]
    assert ranked[0]["score"] == 9.0
    assert ranked[0]["reason"] == "бьёт в главную боль"
    assert ranked[0]["slogan"] == "S2"
    assert ranked[0]["hook_angle"] == "rational"


async def test_rank_includes_candidate_omitted_by_llm(monkeypatch):
    cands = [_cand("c1", "S1"), _cand("c2", "S2")]
    ranking = RankingSet.model_construct(
        ranking=[RankingItem(candidate_id="c1", score=8.0, reason="ok")]
    )
    _patch_skill(monkeypatch)

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return ranking

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)

    out = await mod.rank_candidates(_state(cands))
    ids = [r["id"] for r in out["ranked"]]

    assert set(ids) == {"c1", "c2"}
    assert ids[0] == "c1"  # ranked one first
