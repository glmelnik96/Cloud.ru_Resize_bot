"""HITL: image approve node.

Pauses the graph via ``interrupt()`` after generate_image produces a hero PNG.
PTB layer renders the inline keyboard (OK / regenerate / refine / cancel),
collects the user's decision and resumes the graph with ``Command(resume=...)``.

Decision contract (the value passed to ``Command(resume=...)``):
    {"action": "approve", "comment": None}
    {"action": "regenerate", "comment": None}
    {"action": "refine", "comment": "<free-form text from user>"}
    {"action": "cancel", "comment": None}

Difference vs hitl_text_approve:
- "regenerate" bumps image_revise_round only — text winner is kept.
- "refine" also keeps text winner; the free-form comment goes into
  image_refine_comment for the next generate_image call to consume.
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from graph.state import GeneratedImage, GraphState

log = structlog.get_logger(__name__)


async def hitl_image_approve(state: GraphState) -> dict:
    image = state.get("image")
    if image is None:
        return {"error": "hitl_image_approve called without image"}

    image_obj = (
        image if isinstance(image, GeneratedImage) else GeneratedImage.model_validate(image)
    )

    log.info(
        "hitl_image_interrupt",
        session_id=state.get("session_id"),
        local_path=image_obj.local_path,
        style=image_obj.style,
    )

    decision: dict = interrupt(
        {
            "kind": "image_approve",
            "image": image_obj.model_dump(),
            "session_id": state.get("session_id"),
        }
    )

    log.info(
        "hitl_image_resume",
        session_id=state.get("session_id"),
        action=decision.get("action"),
        has_comment=bool(decision.get("comment")),
    )

    action = decision.get("action", "cancel")
    if action == "approve":
        return {"image_approved": True}
    if action == "cancel":
        return {"image_approved": False, "cancelled": True}
    if action == "regenerate":
        return {
            "image": None,
            "image_revise_round": state.get("image_revise_round", 0) + 1,
            "image_refine_comment": None,
        }
    if action == "refine":
        comment = decision.get("comment") or ""
        return {
            "image": None,
            "image_revise_round": state.get("image_revise_round", 0) + 1,
            "image_refine_comment": comment,
        }
    return {"image_approved": False, "cancelled": True}
