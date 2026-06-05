"""Debounced TG admin notifications from non-handler code (graph nodes, infra).

Why a module-level singleton instead of bot_data:
  Graph nodes don't have direct access to the PTB `Application` / `bot` —
  they receive only `GraphState`. Threading the bot reference through state
  would pollute the schema and the checkpoint payload (state.image / state.brief
  are typed dicts persisted in Redis; sticking a Bot handle in there is wrong).
  Instead we hold the bot handle in this module, set once at `_post_init`,
  cleared at `_post_shutdown`.

Debouncing:
  An infra failure can fire once per `/new` attempt — and the owner may retry
  multiple times before realising something is dead upstream. Without a cooldown
  the admin gets spammed. `notify_admin(..., dedupe_key=..., cooldown_s=3600)`
  makes the same alert at most once per hour per process; clearing on shutdown
  means a docker restart (the natural recovery action) also resets the cooldown.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_bot: Any = None
_admin_id: int | None = None
_last_alert_at: dict[str, float] = {}


def set_admin_notifier(bot: Any, admin_user_id: int | None) -> None:
    """Register the bot + admin chat id. Called from `_post_init`."""
    global _bot, _admin_id
    _bot = bot
    _admin_id = admin_user_id
    log.info(
        "admin_notifier_ready",
        admin_user_id=admin_user_id,
        bot_attached=bot is not None,
    )


def clear_admin_notifier() -> None:
    """Drop the bot reference + reset cooldowns. Called from `_post_shutdown`."""
    global _bot, _admin_id, _last_alert_at
    _bot = None
    _admin_id = None
    _last_alert_at = {}


async def notify_admin(
    text: str,
    *,
    dedupe_key: str | None = None,
    cooldown_s: float = 3600.0,
) -> bool:
    """Send a TG DM to the admin, optionally debounced.

    Returns True if a message was actually sent. Reasons to return False:
      - notifier not registered (e.g. tests, or alert fired before _post_init)
      - admin_user_id is None (WHITELIST_USER_IDS empty)
      - dedupe_key was already alerted within the last cooldown_s seconds
      - the TG send itself raised (logged, swallowed — never break the caller)
    """
    if _bot is None or _admin_id is None:
        log.info(
            "admin_alert_skipped_no_target",
            has_bot=_bot is not None,
            has_admin=_admin_id is not None,
            preview=text[:80],
        )
        return False

    now = time.time()
    if dedupe_key is not None:
        last = _last_alert_at.get(dedupe_key, 0.0)
        if now - last < cooldown_s:
            log.info(
                "admin_alert_suppressed",
                dedupe_key=dedupe_key,
                since_last_s=round(now - last, 1),
                cooldown_s=cooldown_s,
            )
            return False
        _last_alert_at[dedupe_key] = now

    try:
        await _bot.send_message(chat_id=_admin_id, text=text)
    except Exception as exc:  # noqa: BLE001 — never let a notifier failure break callers
        log.warning(
            "admin_alert_send_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            dedupe_key=dedupe_key,
        )
        return False

    log.info("admin_alert_sent", dedupe_key=dedupe_key, length=len(text))
    return True
