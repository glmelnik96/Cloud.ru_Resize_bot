"""App3 redesign — data-model cardinality (TDD RED→GREEN).

The 1→12 redesign (2026-06-21):
- CandidateSet now carries EXACTLY 12 propositions (was 3–5).
- PersonaSet collapses to EXACTLY 1 persona (audience is single; the 12
  propositions are 12 angles into that one persona).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph.state import CandidateSet, MessageCandidate, Persona, PersonaSet


def _candidate(i: int) -> MessageCandidate:
    return MessageCandidate(
        id=f"c{i}", slogan=f"slogan {i}", body=f"body {i}",
        cta="Начать сейчас", hook_angle="rational",
    )


def _persona(seg: str = "DevOps") -> Persona:
    return Persona(
        segment=seg, age_range="25-40", pain_points=["x"],
        motivations=["y"], objections=["z"], communication_style="formal",
    )


def test_candidate_set_requires_exactly_12():
    # dicts, not model instances — production input is always the LLM's JSON
    def dicts(n: int) -> list[dict]:
        return [_candidate(i).model_dump() for i in range(n)]

    assert len(CandidateSet(candidates=dicts(12)).candidates) == 12
    with pytest.raises(ValidationError):
        CandidateSet(candidates=dicts(5))
    with pytest.raises(ValidationError):
        CandidateSet(candidates=dicts(13))


def test_persona_set_is_single():
    assert len(PersonaSet(personas=[_persona()]).personas) == 1
    with pytest.raises(ValidationError):
        PersonaSet(personas=[_persona("A"), _persona("B")])


# ── slogan hard cap (2026-07-10): 42 chars at generation time ──────────
# The cap lives on the GENERATION schema (CandidateSet) so the LLM retry-with-
# feedback loop enforces it, while the base MessageCandidate stays permissive —
# parked checkpoints written before the cap must still coerce on resume.

def _set_with_slogan(slogan: str) -> CandidateSet:
    items = [_candidate(i) for i in range(12)]
    items[0] = items[0].model_copy(update={"slogan": slogan})
    return CandidateSet(candidates=[c.model_dump() for c in items])


def test_candidate_set_accepts_slogan_at_42_chars():
    assert len(_set_with_slogan("x" * 42).candidates) == 12


def test_candidate_set_rejects_slogan_over_42_chars():
    with pytest.raises(ValidationError):
        _set_with_slogan("x" * 43)


def test_base_message_candidate_stays_permissive_for_old_checkpoints():
    c = MessageCandidate(
        slogan="x" * 60, body="b", cta="Начать", hook_angle="rational"
    )
    assert len(c.slogan) == 60


def test_explorer_prompt_declares_42_char_hard_limit():
    from pathlib import Path

    body = Path("prompts/creative_ads_explorer.md").read_text(encoding="utf-8")
    assert "42" in body
