"""rank_candidates node — GLM-4.7 thinking-OFF.

ONE light LLM call that orders the 12 propositions by predicted resonance with
the single persona and attaches a one-line «почему зайдёт ЦА» to each. Replaces
the heavy evaluate_as_persona_loop (12×N verdicts + winner/revise). There is no
winner: the whole ordered set is handed to HITL (the user approves the set).

Input:
  - GraphState.brief (AdBrief) — incl. emotion
  - GraphState.personas (list[Persona]) — single persona; personas[0] is used
  - GraphState.candidates (list[MessageCandidate]) — the 12 propositions
Output:
  - GraphState.ranked (list[dict]) — best-first; each item is the candidate dict
    merged with `score` and `reason`.
"""

from __future__ import annotations

import structlog

from graph.agent_runner import run_agent
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    GraphState,
    MessageCandidate,
    Persona,
    RankingSet,
)

log = structlog.get_logger(__name__)

_AGENT_ID = "rank_candidates"
_SKILL_NAME = "rank_candidates"


async def rank_candidates(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    candidates_raw = state.get("candidates") or []
    if not candidates_raw:
        raise ValueError("rank_candidates: state.candidates is empty")
    candidates = [_coerce(c, MessageCandidate, "candidate") for c in candidates_raw]

    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("rank_candidates: state.personas is empty")
    persona = _coerce(personas_raw[0], Persona, "persona")

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    cards_block = "\n".join(
        f"- id={c.id} | slogan: {c.slogan} | cta: {c.cta} | hook: {c.hook_angle} | body: {c.body}"
        for c in candidates
    )

    user_msg = _render(
        user_tpl,
        **{
            "brief.product": brief.product,
            "brief.emotion": brief.emotion or "(не задано)",
            "persona.segment": persona.segment,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.motivations": ", ".join(persona.motivations),
            "persona.objections": ", ".join(persona.objections),
            "persona.communication_style": persona.communication_style,
            "candidates_block": cards_block,
        },
    )

    result: RankingSet = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=RankingSet,
        session_id=state.get("session_id"),
    )

    ranked = _merge(candidates, result)

    log.info(
        "rank_candidates_ok",
        session_id=state.get("session_id"),
        n=len(ranked),
        order=[r["id"] for r in ranked],
    )
    return {"ranked": ranked}


def _merge(candidates: list[MessageCandidate], result: RankingSet) -> list[dict]:
    """Order candidates best-first by the ranker's score and attach reason.

    Candidates the LLM omitted from the ranking are appended at the end (score 0)
    so the full set always reaches the user.
    """
    by_id = {c.id: c for c in candidates}
    ranked: list[dict] = []
    seen: set[str] = set()
    for item in sorted(result.ranking, key=lambda r: r.score, reverse=True):
        cand = by_id.get(item.candidate_id)
        if cand is None or cand.id in seen:
            continue
        seen.add(cand.id)
        ranked.append({**cand.model_dump(), "score": item.score, "reason": item.reason})
    for c in candidates:
        if c.id not in seen:
            ranked.append({**c.model_dump(), "score": 0.0, "reason": ""})
    return ranked


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
