"""evaluate_as_persona_loop node — GLM-5.1 thinking-ON.

For each (candidate × persona) pair, simulate persona reaction → Verdict.
Aggregate per-candidate score. Pick winner or trigger revise (≤2 rounds).

Concurrency: asyncio.Semaphore(3) — Cloud.ru caps at 20 RPS per API key,
GLM thinking-ON calls run for ~5-15s each; 3 concurrent keeps headroom.

The aggregation:
    score = 0.4*mean(resonance) + 0.3*mean(action_intent) + 0.3*mean(clarity)

For B-variant (state.persona_priority == 1) and >=2 personas, weights shift:
    persona[1].score weight = 0.6, persona[0].score weight = 0.4

Consultant-mode guard (non_consultant_rate metric, prompts/persona_eval.md):
verdicts whose free-text fields contain advisory/marketing-consultant phrasing
are detected via graph.hooks.is_consultant_mode and enter the aggregation with
CONSULTANT_MODE_WEIGHT instead of being dropped.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict

import structlog

from agents.registry import load_agent
from graph.agent_runner import run_agent
from graph.hooks import is_consultant_mode
from graph.nodes.parse_brief import _extract_section, _render
from graph.prompts import load_skill
from graph.state import (
    AdBrief,
    GraphState,
    MessageCandidate,
    Persona,
    Verdict,
)
from llm.cloudru import get_shared_client

log = structlog.get_logger(__name__)

_AGENT_ID = "evaluate_as_persona_loop"
_SKILL_NAME = "persona_eval"

# Weight applied to a verdict whose text fields show consultant-mode phrasing
# (the LLM slipped out of persona role into "marketing advisor" speech).
# 0.5 = the verdict still participates in ranking but contributes half of its
# score; we deliberately do NOT drop it — scores may still carry signal even
# when the free-form text broke role.
CONSULTANT_MODE_WEIGHT = 0.5


async def evaluate_as_persona_loop(state: GraphState) -> dict:
    brief = _coerce(state.get("brief"), AdBrief, "brief")
    candidates_raw = state.get("candidates") or []
    if not candidates_raw:
        raise ValueError("evaluate_as_persona_loop: state.candidates empty")
    candidates = [_coerce(c, MessageCandidate, "candidate") for c in candidates_raw]
    personas_raw = state.get("personas") or []
    personas = [_coerce(p, Persona, "persona") for p in personas_raw]
    persona_priority = state.get("persona_priority", 0)

    skill = load_skill(_SKILL_NAME)
    system_tpl = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    card = load_agent(_AGENT_ID)
    max_concurrency = int(card.extra.get("concurrency", {}).get("semaphore", 3))
    revise_cfg = card.extra.get("revise", {})
    revise_threshold = float(revise_cfg.get("threshold", 6.5))
    max_revise_rounds = int(revise_cfg.get("max_rounds", 2))

    client = get_shared_client()
    sem = asyncio.Semaphore(max_concurrency)

    async def one_verdict(candidate: MessageCandidate, persona: Persona) -> Verdict:
        system_msg = _render(
            system_tpl,
            **{
                "persona.segment": persona.segment,
                "persona.age_range": persona.age_range,
                "persona.pain_points": ", ".join(persona.pain_points),
                "persona.motivations": ", ".join(persona.motivations),
                "persona.objections": ", ".join(persona.objections),
                "persona.communication_style": persona.communication_style,
            },
        )
        user_msg = _render(
            user_tpl,
            **{
                "brief.channel": brief.channel,
                "brief.product": brief.product,
                "candidate.id": candidate.id,
                "candidate.slogan": candidate.slogan,
                "candidate.body": candidate.body,
                "candidate.cta": candidate.cta,
            },
        )
        async with sem:
            verdict = await run_agent(
                _AGENT_ID,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                schema=Verdict,
                client=client,
                session_id=state.get("session_id"),
            )
        # Patch identifiers — the LLM sometimes echoes back its own values.
        return verdict.model_copy(
            update={
                "candidate_id": candidate.id,
                "persona_segment": persona.segment,
            }
        )

    tasks = [one_verdict(c, p) for c in candidates for p in personas]
    verdicts: list[Verdict] = await asyncio.gather(*tasks)

    for v in verdicts:
        if _verdict_weight(v) < 1.0:
            log.info(
                "consultant_mode_detected",
                agent=_AGENT_ID,
                session_id=state.get("session_id"),
                candidate_id=v.candidate_id,
                persona_segment=v.persona_segment,
                weight=CONSULTANT_MODE_WEIGHT,
                free_form_reaction=v.free_form_reaction,
            )

    scores = _aggregate(verdicts, candidates, personas, persona_priority)
    winner_id = max(scores, key=lambda k: scores[k])
    winner = next(c for c in candidates if c.id == winner_id)
    best_score = scores[winner_id]

    revise_round = state.get("revise_round", 0)
    needs_revise = (
        best_score < revise_threshold and revise_round < max_revise_rounds
    )

    log.info(
        "persona_loop_done",
        session_id=state.get("session_id"),
        revise_round=revise_round,
        n_candidates=len(candidates),
        n_personas=len(personas),
        scores={cid: round(s, 2) for cid, s in scores.items()},
        winner=winner_id,
        winner_score=round(best_score, 2),
        needs_revise=needs_revise,
    )

    return {
        "verdicts": [v.model_dump() for v in verdicts],
        "candidate_scores": scores,
        "winner": None if needs_revise else winner.model_dump(),
        "revise_round": revise_round + 1 if needs_revise else revise_round,
    }


def _verdict_weight(verdict: Verdict) -> float:
    """Trust weight of a single verdict in the aggregation.

    Consultant-mode verdicts (advisory phrasing in free_form_reaction or
    main_friction instead of persona speech) get CONSULTANT_MODE_WEIGHT,
    everything else gets 1.0.
    """
    if is_consultant_mode(verdict.free_form_reaction) or is_consultant_mode(
        verdict.main_friction
    ):
        return CONSULTANT_MODE_WEIGHT
    return 1.0


def _aggregate(
    verdicts: list[Verdict],
    candidates: list[MessageCandidate],
    personas: list[Persona],
    persona_priority: int,
) -> dict[str, float]:
    """Per-candidate weighted score across personas."""
    # group by candidate -> persona_segment -> [verdicts]
    by_cand: dict[str, dict[str, list[Verdict]]] = defaultdict(lambda: defaultdict(list))
    for v in verdicts:
        by_cand[v.candidate_id][v.persona_segment].append(v)

    persona_weights = _persona_weights(personas, persona_priority)

    scores: dict[str, float] = {}
    for c in candidates:
        per_persona_scores: list[tuple[float, float]] = []  # (weight, score)
        for p in personas:
            vs = by_cand[c.id].get(p.segment, [])
            if not vs:
                continue
            # Each verdict contributes its score scaled by its trust weight
            # but keeps a full slot in the denominator: a consultant-mode
            # verdict actively lowers the candidate's score instead of being
            # silently renormalized away (which would make the downweight a
            # no-op in the common 1-verdict-per-(candidate, persona) case).
            mean_score = statistics.mean(
                _verdict_weight(v)
                * (0.4 * v.resonance + 0.3 * v.action_intent + 0.3 * v.clarity)
                for v in vs
            )
            per_persona_scores.append((persona_weights[p.segment], mean_score))

        if not per_persona_scores:
            scores[c.id] = 0.0
            continue
        total_w = sum(w for w, _ in per_persona_scores) or 1.0
        scores[c.id] = sum(w * s for w, s in per_persona_scores) / total_w
    return scores


def _persona_weights(personas: list[Persona], priority: int) -> dict[str, float]:
    """Equal weights unless A/B variant B (priority=1) with >=2 personas."""
    if len(personas) < 2 or priority == 0:
        return {p.segment: 1.0 for p in personas}
    # B variant: persona[1] dominates ranking
    weights = {p.segment: 0.4 for p in personas}
    weights[personas[1].segment] = 0.6
    weights[personas[0].segment] = 0.4
    return weights


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
