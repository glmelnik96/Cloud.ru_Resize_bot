"""Helpers shared across graph nodes."""
from __future__ import annotations

from graph.state import GraphState, MessageCandidate


def chosen_candidate(state: GraphState) -> MessageCandidate:
    """The single proposition composed onto the hero banner.

    Block 1 (2026-06-21) dropped the single ``winner`` in favour of 12 selected
    propositions. The image stage composes ONE proposition's text per hero, so
    we pick the top-ranked one (``ranked`` is ordered best-first by
    select_by_persona). Selection-only keys (score/reason) on the dict are ignored
    by MessageCandidate.
    """
    ranked = state.get("ranked") or []
    if not ranked:
        raise ValueError("chosen_candidate: state.ranked is empty")
    top = ranked[0]
    if isinstance(top, MessageCandidate):
        return top
    return MessageCandidate.model_validate(top)


def ranked_candidates(state: GraphState) -> list[MessageCandidate]:
    """The 12 selected propositions, ordered best-first (per select_by_persona).

    The 12-banner stage (2026-06-21) composes one banner per proposition, so
    every node downstream of the selection iterates this list. Selection-only
    keys (score/reason) on each dict are ignored by MessageCandidate.
    """
    ranked = state.get("ranked") or []
    if not ranked:
        raise ValueError("ranked_candidates: state.ranked is empty")
    return [
        c if isinstance(c, MessageCandidate) else MessageCandidate.model_validate(c)
        for c in ranked
    ]
