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
    assert len(CandidateSet(candidates=[_candidate(i) for i in range(12)]).candidates) == 12
    with pytest.raises(ValidationError):
        CandidateSet(candidates=[_candidate(i) for i in range(5)])
    with pytest.raises(ValidationError):
        CandidateSet(candidates=[_candidate(i) for i in range(13)])


def test_persona_set_is_single():
    assert len(PersonaSet(personas=[_persona()]).personas) == 1
    with pytest.raises(ValidationError):
        PersonaSet(personas=[_persona("A"), _persona("B")])
