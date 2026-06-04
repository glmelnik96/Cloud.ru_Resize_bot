"""Drives a compiled LangGraph instance for a single TG session.

Lifecycle:
1. ``start_session`` is awaited as a background task after wizard ends.
   It invokes the graph; the graph runs until ``hitl_text_approve`` calls
   ``interrupt()``. We then render the winner in TG with inline buttons.
2. Inline button taps go to ``on_hitl_callback``. For approve/cancel/regenerate
   we resume the graph immediately. For refine we ask for a free-form comment
   first (MessageHandler ``on_refine_text``).
3. After the graph reaches a terminal state we send a summary message and
   release the session slot.
"""

from __future__ import annotations

import structlog
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.sessions import Session, drop, get_active, put

log = structlog.get_logger(__name__)


# ----- bootstrap: compiled graph holder -------------------------------------


GRAPH_KEY = "compiled_graph"
CHECKPOINTER_CM_KEY = "_checkpointer_cm"


async def init_compiled_graph(app: Application) -> None:
    """Open AsyncRedisSaver and compile the text graph once at startup."""
    import os

    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    from graph.builder import build_text_graph

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    log.info("graph_init", redis_url=redis_url)

    cm = AsyncRedisSaver.from_conn_string(redis_url)
    saver = await cm.__aenter__()
    await saver.asetup()
    compiled = build_text_graph().compile(checkpointer=saver)

    app.bot_data[GRAPH_KEY] = compiled
    app.bot_data[CHECKPOINTER_CM_KEY] = cm
    log.info("graph_ready")


async def shutdown_compiled_graph(app: Application) -> None:
    cm = app.bot_data.pop(CHECKPOINTER_CM_KEY, None)
    if cm is not None:
        await cm.__aexit__(None, None, None)
    app.bot_data.pop(GRAPH_KEY, None)
    log.info("graph_shutdown")


def _graph(app: Application):
    g = app.bot_data.get(GRAPH_KEY)
    if g is None:
        raise RuntimeError("compiled graph not initialised — call init_compiled_graph in post_init")
    return g


def _config(session: Session) -> dict:
    return {"configurable": {"thread_id": session.thread_id}, "recursion_limit": 30}


# ----- starting & resuming --------------------------------------------------


async def start_session(app: Application, session: Session) -> None:
    """Invoke the graph for the first time. Runs until interrupt or END."""
    initial = {
        "session_id": session.thread_id,
        "user_id": session.user_id,
        "raw_brief": session.wizard_data.get("raw_brief", ""),
        "revise_round": 0,
        "prior_variant": session.prior_variant,
    }
    put(app.bot_data, session)
    try:
        final = await _graph(app).ainvoke(initial, config=_config(session))
        await _handle_terminal_or_interrupt(app, session, final)
    except Exception as exc:  # noqa: BLE001
        log.exception("graph_start_failed", thread_id=session.thread_id)
        await app.bot.send_message(
            chat_id=session.chat_id,
            text=f"Сбой генерации: {type(exc).__name__}. Попробуй /new заново.",
        )
        drop(app.bot_data, session.user_id)


async def _resume(app: Application, session: Session, decision: dict) -> None:
    try:
        final = await _graph(app).ainvoke(
            Command(resume=decision), config=_config(session)
        )
        await _handle_terminal_or_interrupt(app, session, final)
    except Exception as exc:  # noqa: BLE001
        log.exception("graph_resume_failed", thread_id=session.thread_id)
        await app.bot.send_message(
            chat_id=session.chat_id,
            text=f"Сбой при возобновлении: {type(exc).__name__}.",
        )
        drop(app.bot_data, session.user_id)


async def _handle_terminal_or_interrupt(
    app: Application, session: Session, final: dict
) -> None:
    interrupts = final.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        kind = payload.get("kind") if isinstance(payload, dict) else None
        if kind == "image_approve":
            await _render_image_approve(app, session, payload)
        else:
            await _render_text_approve(app, session, payload)
        return

    # terminal
    if final.get("cancelled"):
        await app.bot.send_message(chat_id=session.chat_id, text="Сессия отменена.")
        drop(app.bot_data, session.user_id)
        return

    zip_path = final.get("rendered_zip_path")
    if zip_path:
        await _deliver_zip(app, session, final, zip_path)
        return

    # safety net: text approved but image pipeline didn't finish
    if final.get("text_approved"):
        await app.bot.send_message(
            chat_id=session.chat_id,
            text=(
                "Текст утверждён, но пайплайн картинки/рендера не дошёл до ZIP. "
                "Логи сессии: " + session.thread_id
            ),
        )
        drop(app.bot_data, session.user_id)
        return

    # graph ended without hitl decision (unexpected)
    await app.bot.send_message(
        chat_id=session.chat_id,
        text="Граф завершился без явного решения. Лог сессии: " + session.thread_id,
    )
    drop(app.bot_data, session.user_id)


async def _deliver_zip(
    app: Application, session: Session, final: dict, zip_path: str
) -> None:
    winner = final.get("winner") or {}
    if isinstance(winner, dict):
        slogan = winner.get("slogan", "")
        hook = winner.get("hook_angle", "")
    else:
        slogan = getattr(winner, "slogan", "")
        hook = getattr(winner, "hook_angle", "")
    image = final.get("image") or {}
    style = image.get("style") if isinstance(image, dict) else getattr(image, "style", "")
    files = final.get("rendered_files") or []
    formats = ", ".join(r.get("format", "?") for r in files) or "(пусто)"

    caption = (
        "Готово.\n"
        f"slogan: {slogan}\n"
        f"hook: {hook}\n"
        f"style: {style}\n"
        f"formats: {formats}"
    )
    try:
        with open(zip_path, "rb") as fh:
            await app.bot.send_document(
                chat_id=session.chat_id,
                document=fh,
                filename=zip_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                caption=caption,
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("zip_send_failed", thread_id=session.thread_id, zip_path=zip_path)
        await app.bot.send_message(
            chat_id=session.chat_id,
            text=f"ZIP сформирован, но отправка не удалась: {type(exc).__name__}. Путь: {zip_path}",
        )

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Сделать вариант B (A/B)", callback_data=f"ab:{session.thread_id}")]]
    )
    await app.bot.send_message(
        chat_id=session.chat_id,
        text="Запустить вариант B для сравнения?",
        reply_markup=kb,
    )

    ab_store = app.bot_data.setdefault("ab_prior", {})
    ab_store[session.thread_id] = {
        "slogan": slogan,
        "hook_angle": hook,
        "persona_priority": final.get("persona_priority", 0),
    }
    session.status = "done"


async def _render_text_approve(
    app: Application, session: Session, payload: dict
) -> None:
    candidate = payload.get("candidate") or {}
    text = (
        "Текст-кандидат от персона-loop:\n\n"
        f"slogan: {candidate.get('slogan')}\n"
        f"body: {candidate.get('body')}\n"
        f"cta: {candidate.get('cta')}\n"
        f"hook: {candidate.get('hook_angle')}\n\n"
        "Что делаем?"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("OK, текст принят", callback_data="hitl:approve")],
            [InlineKeyboardButton("Перегенерить", callback_data="hitl:regenerate")],
            [InlineKeyboardButton("Доработать (комментарием)", callback_data="hitl:refine")],
            [InlineKeyboardButton("Отменить", callback_data="hitl:cancel")],
        ]
    )
    msg = await app.bot.send_message(chat_id=session.chat_id, text=text, reply_markup=kb)
    session.hitl_message_id = msg.message_id
    session.status = "awaiting_hitl"
    put(app.bot_data, session)


async def _render_image_approve(
    app: Application, session: Session, payload: dict
) -> None:
    image = payload.get("image") or {}
    local_path = image.get("local_path")
    style = image.get("style", "?")
    caption = (
        f"Hero-картинка (style: {style}).\nЧто делаем?"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("OK, картинка принята", callback_data="img:approve")],
            [InlineKeyboardButton("Перегенерить", callback_data="img:regenerate")],
            [InlineKeyboardButton("Доработать (комментарием)", callback_data="img:refine")],
            [InlineKeyboardButton("Отменить", callback_data="img:cancel")],
        ]
    )
    if local_path:
        try:
            with open(local_path, "rb") as fh:
                msg = await app.bot.send_photo(
                    chat_id=session.chat_id,
                    photo=fh,
                    caption=caption,
                    reply_markup=kb,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("image_send_failed", thread_id=session.thread_id, local_path=local_path)
            msg = await app.bot.send_message(
                chat_id=session.chat_id,
                text=f"Картинка готова, но превью не отправилось ({type(exc).__name__}). Путь: {local_path}",
                reply_markup=kb,
            )
    else:
        msg = await app.bot.send_message(chat_id=session.chat_id, text=caption, reply_markup=kb)
    session.hitl_message_id = msg.message_id
    session.status = "awaiting_image_hitl"
    put(app.bot_data, session)


# ----- handlers -------------------------------------------------------------


async def on_hitl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    await q.answer()
    user = update.effective_user
    if user is None:
        return
    session = get_active(context.application.bot_data, user.id)
    if session is None or session.status != "awaiting_hitl":
        await q.edit_message_text("Эта сессия больше не активна.")
        return

    action = q.data.split(":", 1)[1]
    if action == "refine":
        session.status = "awaiting_refine"
        put(context.application.bot_data, session)
        await q.edit_message_text("Опиши одной-двумя фразами, что переделать.")
        return

    await q.edit_message_text(f"Выбрано: {action}. Возобновляю граф...")
    decision = {"action": action, "comment": None}
    context.application.create_task(_resume(context.application, session, decision))


async def on_image_hitl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    await q.answer()
    user = update.effective_user
    if user is None:
        return
    session = get_active(context.application.bot_data, user.id)
    if session is None or session.status != "awaiting_image_hitl":
        await _edit_hitl_status(q, "Эта сессия больше не активна.")
        return

    action = q.data.split(":", 1)[1]
    if action == "refine":
        session.status = "awaiting_image_refine"
        put(context.application.bot_data, session)
        await _edit_hitl_status(q, "Опиши одной-двумя фразами, что переделать в картинке.")
        return

    await _edit_hitl_status(q, f"Картинка: {action}. Возобновляю граф...")
    decision = {"action": action, "comment": None}
    context.application.create_task(_resume(context.application, session, decision))


async def _edit_hitl_status(q, text: str) -> None:
    """Edit a HITL prompt's caption or text — whichever the original message has.

    Image HITL is a photo with caption; text HITL is a plain text message.
    Telegram errors out if you try to edit_message_text on a photo (no text field).
    Falls back to dropping the keyboard if both edit kinds fail.
    """
    try:
        msg = q.message
        if msg is not None and getattr(msg, "photo", None):
            await q.edit_message_caption(caption=text)
        else:
            await q.edit_message_text(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("hitl_status_edit_failed", error=str(exc))
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


async def on_refine_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    user = update.effective_user
    if user is None:
        return
    session = get_active(context.application.bot_data, user.id)
    if session is None or session.status not in {"awaiting_refine", "awaiting_image_refine"}:
        return  # not our message
    comment = update.message.text.strip()
    await update.message.reply_text("Комментарий принят. Возобновляю граф...")
    session.status = "running"
    put(context.application.bot_data, session)
    decision = {"action": "refine", "comment": comment}
    context.application.create_task(_resume(context.application, session, decision))


async def on_ab_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    await q.answer()
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return
    existing = get_active(context.application.bot_data, user.id)
    if existing is not None and existing.status not in {"done", "cancelled"}:
        await q.edit_message_text("Сначала заверши текущую сессию.")
        return

    _, prev_thread = q.data.split(":", 1)
    ab_store = context.application.bot_data.get("ab_prior", {})
    prior = ab_store.get(prev_thread)
    if prior is None:
        await q.edit_message_text("Данные A-варианта потеряны — запусти /new заново.")
        return

    # spawn a B-variant session reusing the same wizard_data — but raw_brief
    # is not stored on the user beyond wizard, so we just bump persona_priority
    # if there are >=2 personas. Image isn't built yet, so for M2 B-variant
    # only diversifies text axis.
    if existing is None:
        await q.edit_message_text(
            "Для варианта B нужен исходный бриф. Запусти /new и пройди мастер ещё раз — он подхватит prior_variant."
        )
        return
    await q.edit_message_text("M2: вариант B пока запускается только тем же /new — сохраню prior_variant в bot_data.")
    # full B-variant orchestration is M4 scope


def register_runner_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(on_hitl_callback, pattern=r"^hitl:(approve|regenerate|refine|cancel)$"))
    app.add_handler(CallbackQueryHandler(on_image_hitl_callback, pattern=r"^img:(approve|regenerate|refine|cancel)$"))
    app.add_handler(CallbackQueryHandler(on_ab_callback, pattern=r"^ab:"))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_refine_text,
        ),
        group=1,  # runs after wizard MessageHandlers (group 0)
    )
