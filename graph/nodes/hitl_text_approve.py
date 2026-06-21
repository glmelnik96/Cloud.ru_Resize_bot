"""HITL: text approve node.

Pauses the graph via ``interrupt()`` after rank_candidates orders the 12
propositions. The UI renders all 12 ranked cards; the user approves the SET,
asks to regenerate the whole set, or cancels. There is no single winner and no
per-candidate refine (App3 redesign 2026-06-21).

Decision contract (the value passed to ``Command(resume=...)``):
    {"action": "approve"}     — accept the whole set of 12, proceed
    {"action": "regenerate"}  — throw away the set, generate 12 fresh angles
    {"action": "cancel"}      — abort the run
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from graph.state import GraphState

log = structlog.get_logger(__name__)


async def hitl_text_approve(state: GraphState) -> dict:
    ranked = state.get("ranked") or []

    log.info(
        "hitl_text_interrupt",
        session_id=state.get("session_id"),
        n=len(ranked),
    )

    decision: dict = interrupt(
        {
            "kind": "text_approve",
            "candidates": ranked,
            "session_id": state.get("session_id"),
        }
    )

    action = decision.get("action", "cancel")
    log.info(
        "hitl_text_resume",
        session_id=state.get("session_id"),
        action=action,
    )

    if action == "approve":
        return {"text_approved": True}
    if action == "regenerate":
        # drop the set so generate_message_candidates produces a fresh 12.
        return {"candidates": [], "ranked": []}
    # cancel (and any unknown action) aborts the run
    return {"text_approved": False, "cancelled": True}
