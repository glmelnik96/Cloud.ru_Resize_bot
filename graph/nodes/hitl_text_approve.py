"""HITL: text approve node.

Pauses the graph via ``interrupt()`` after select_by_persona picks the 12
propositions. The UI renders all 12 ranked cards; the user approves the SET,
asks to regenerate the whole set, or cancels. There is no single winner and no
per-candidate refine (App3 redesign 2026-06-21).

Decision contract (the value passed to ``Command(resume=...)``):
    {"action": "approve"}                      — accept the set, ranked[0] остаётся главным
    {"action": "approve", "winner_id": "..."}  — пометить победителя; ranked переставляется
                                                 так, что победитель встаёт в ranked[0];
                                                 неизвестный winner_id — fail-open (порядок
                                                 скоринговый, warning в лог)
    {"action": "regenerate"}  — throw away the set, generate 12 fresh angles
    {"action": "cancel"}      — abort the run
    {"action": "timeout"}     — nobody came back; the service gave up waiting
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
        winner_id = decision.get("winner_id")
        if winner_id:
            idx = next(
                (i for i, c in enumerate(ranked) if c.get("id") == winner_id), None
            )
            if idx is None:
                # API валидирует раньше; здесь fail-open — скоринговый порядок.
                log.warning(
                    "winner_id_unknown",
                    session_id=state.get("session_id"),
                    winner_id=winner_id,
                )
                winner_id = None
            elif idx > 0:
                ranked = [ranked[idx], *ranked[:idx], *ranked[idx + 1 :]]
        # Даунстрим живёт конвенцией «ranked[0] — главный»: победитель встаёт
        # в голову списка, и метафора/рендер получают его без своих изменений.
        return {"text_approved": True, "ranked": ranked, "winner_id": winner_id}
    if action == "regenerate":
        # drop the set so generate_message_candidates produces a fresh 12.
        return {"candidates": [], "ranked": []}
    if action == "timeout":
        # Abandoned session, not a "no thanks" — mark it so the terminal row
        # carries reason=timeout instead of reason=user.
        log.warning("hitl_text_approve_timeout", session_id=state.get("session_id"))
        return {"text_approved": False, "cancelled": True, "error": "text_approve_timeout"}
    # cancel (and any unknown action) aborts the run
    return {"text_approved": False, "cancelled": True}
