"""HITL: image upload node — M3.3 replacement for hitl_image_approve.

In M3.3 we no longer auto-generate the hero PNG. Instead:
  1. generate_image_prompt produces an EN prompt that goes to the user.
  2. THIS node pauses the graph via ``interrupt()``.
  3. The PTB layer shows the prompt in TG and waits for the user to upload
     a photo (compressed) or an image-MIME Document (uncompressed PNG).
  4. After download, PTB resumes with ``Command(resume={"action": "upload",
     "local_path": ...})`` and the node populates state.image.

Decision contract (the value passed to ``Command(resume=...)``):
    {"action": "upload", "local_path": str, "style": str, "prompt": str}
    {"action": "cancel"}      # user typed /cancel or pressed inline cancel
    {"action": "timeout"}     # 24h elapsed without upload
    {"action": "metaphor", "comment": str}
        # маркетолог оставил комментарий о метафоре — граф зациклится
        # на generate_image_prompt и перегенерирует ТОЛЬКО образ победителя

There is no revision loop (regenerate/refine) here for direct uploads.
If the user wants a different prompt or image, they cancel and run /new
— or leave a metaphor comment to re-spin only the winner's image prompt
(1 LLM call instead of N). Re-uploads BEFORE the first valid upload are
handled by the PTB layer (latest-wins) before this node ever resumes.
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from graph.state import GeneratedImage, GraphState

log = structlog.get_logger(__name__)


async def hitl_image_upload(state: GraphState) -> dict:
    image_prompt = state.get("image_prompt")
    image_style = state.get("image_style") or "photo"

    if not image_prompt:
        # Shouldn't happen — generate_image_prompt runs immediately upstream.
        return {"error": "hitl_image_upload called without image_prompt"}

    log.info(
        "hitl_image_upload_interrupt",
        session_id=state.get("session_id"),
        style=image_style,
        prompt_len=len(image_prompt),
    )

    # Метафора победителя — то, что человек обсуждает на этой остановке.
    # Пусто, если generate_image_prompt по какой-то причине не отдал meta:
    # экран тогда просто показывает промпт, как до 2026-08-07.
    meta = (state.get("metaphor_meta") or [{}])[0] or {}

    decision: dict = interrupt(
        {
            "kind": "image_upload",
            "image_prompt": image_prompt,
            "image_style": image_style,
            "metaphor": meta.get("metaphor", ""),
            "intended_inference": meta.get("intended_inference", ""),
            "anti_reading": meta.get("anti_reading", ""),
            "session_id": state.get("session_id"),
        }
    )

    action = decision.get("action", "cancel")
    log.info(
        "hitl_image_upload_resume",
        session_id=state.get("session_id"),
        action=action,
    )

    if action == "metaphor":
        comment = (decision.get("comment") or "").strip()
        if not comment:
            log.warning(
                "metaphor_comment_empty",
                session_id=state.get("session_id"),
            )
            # Empty comment = no-op. Signal the router to re-interrupt at this
            # node rather than falling through to fill_templates_per_format
            # (which would crash because no image has been uploaded yet).
            # image_action_pending is cleared by every branch that actually
            # leaves this stop (upload, non-empty metaphor, cancel/timeout).
            return {"metaphor_comment": None, "image_action_pending": True}
        history = [*(state.get("metaphor_comments") or []), comment]
        return {
            "metaphor_comment": comment,
            "metaphor_comments": history,
            "image_action_pending": False,
        }

    if action == "upload":
        # Generation path: a list of heroes (one per proposition) was produced
        # server-side and resumed here -> state.generated_heroes.
        heroes = decision.get("heroes")
        if heroes:
            return {"generated_heroes": list(heroes), "image_action_pending": False}
        local_path = decision.get("local_path")
        if not local_path:
            return {
                "error": "image upload resumed without local_path",
                "cancelled": True,
                "image_action_pending": False,
            }
        image = GeneratedImage(
            url=None,
            local_path=local_path,
            style=decision.get("style", image_style),
            variant="default",
            prompt=decision.get("prompt", image_prompt),
        )
        return {"image": image.model_dump(), "image_action_pending": False}

    if action == "timeout":
        log.warning(
            "hitl_image_upload_timeout",
            session_id=state.get("session_id"),
        )
        return {"cancelled": True, "error": "image_upload_timeout", "image_action_pending": False}

    # cancel (default)
    return {"cancelled": True, "image_action_pending": False}
