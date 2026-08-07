"""HITL: persona approve — остановка «Кому пишем» (спека 2026-08-07).

Пауза после derive_persona: маркетолог видит персону (боли/мотивации/
возражения — редактируемые списки) и плашку kb_match. Правка прямая, без
повторного LLM-вызова: resume несёт готовую персону.

Decision contract (Command(resume=...)):
    {"action": "approve"}                    — персона ок, дальше
    {"action": "approve", "persona": {...}}  — правленая персона (Pydantic Persona)
    {"action": "regenerate"}                 — persona не годится целиком, заново
    {"action": "cancel"} / {"action": "timeout"}
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt
from pydantic import ValidationError

from graph.state import GraphState, Persona

log = structlog.get_logger(__name__)


async def hitl_persona_approve(state: GraphState) -> dict:
    personas = state.get("personas") or []
    persona = personas[0] if personas else None

    log.info("hitl_persona_interrupt", session_id=state.get("session_id"))
    decision: dict = interrupt(
        {
            "kind": "persona_approve",
            "persona": persona,
            "kb_match": state.get("kb_match"),
            "session_id": state.get("session_id"),
        }
    )

    action = decision.get("action", "cancel")
    log.info(
        "hitl_persona_resume", session_id=state.get("session_id"), action=action
    )

    if action == "approve":
        edited = decision.get("persona")
        if edited:
            # API валидирует раньше; узел fail-open — мусор не роняет граф.
            try:
                ok = Persona.model_validate(edited)
                return {"personas": [ok.model_dump()], "persona_approved": True}
            except ValidationError:
                log.warning(
                    "persona_edit_invalid", session_id=state.get("session_id")
                )
        return {"persona_approved": True}
    if action == "regenerate":
        return {"personas": [], "persona_approved": False}
    if action == "timeout":
        log.warning(
            "hitl_persona_approve_timeout", session_id=state.get("session_id")
        )
        return {"cancelled": True, "error": "persona_approve_timeout"}
    # cancel (и любое неизвестное действие) — прерываем запуск
    return {"cancelled": True}
