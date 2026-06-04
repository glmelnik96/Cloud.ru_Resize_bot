"""HITL: text approve node.

Pauses the graph via ``interrupt()`` after persona-loop picks a winner.
PTB layer renders the inline keyboard (OK / regenerate / refine / cancel),
collects the user's decision and resumes the graph with ``Command(resume=...)``.

Decision contract (the value passed to ``Command(resume=...)``):
    {"action": "approve", "comment": None}
    {"action": "regenerate", "comment": None}
    {"action": "refine", "comment": "<free-form text from user>"}
    {"action": "cancel", "comment": None}
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from graph.state import GraphState, MessageCandidate

log = structlog.get_logger(__name__)


async def hitl_text_approve(state: GraphState) -> dict:
    winner = state.get("winner")
    if winner is None:
        # Should never reach here — builder routes here only when winner != None.
        return {"error": "hitl_text_approve called without winner"}

    winner_obj = (
        winner
        if isinstance(winner, MessageCandidate)
        else MessageCandidate.model_validate(winner)
    )

    log.info(
        "hitl_text_interrupt",
        session_id=state.get("session_id"),
        candidate_id=winner_obj.id,
    )

    decision: dict = interrupt(
        {
            "kind": "text_approve",
            "candidate": winner_obj.model_dump(),
            "session_id": state.get("session_id"),
        }
    )

    log.info(
        "hitl_text_resume",
        session_id=state.get("session_id"),
        action=decision.get("action"),
        has_comment=bool(decision.get("comment")),
    )

    action = decision.get("action", "cancel")
    if action == "approve":
        return {"text_approved": True}
    if action == "cancel":
        return {"text_approved": False, "cancelled": True}
    if action == "regenerate":
        # bump revise_round so generate node knows it's a redo
        return {
            "winner": None,
            "revise_round": state.get("revise_round", 0) + 1,
            "candidates": [],
            "verdicts": [],
        }
    if action == "refine":
        comment = decision.get("comment") or ""
        return {
            "winner": None,
            "revise_round": state.get("revise_round", 0) + 1,
            "refine_comment": comment,
            "candidates": [],
            "verdicts": [],
        }
    return {"text_approved": False, "cancelled": True}
