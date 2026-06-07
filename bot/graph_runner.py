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

import asyncio
from datetime import datetime
from pathlib import Path

import structlog
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.sessions import Session, drop, get_active, put

_HEROES_DIR = Path("/data/heroes")
_IMAGE_UPLOAD_TIMEOUT_S = 86400  # 24h
_IMAGE_UPLOAD_TIMEOUTS_KEY = "_image_upload_timeouts"

log = structlog.get_logger(__name__)


# Per-node label shown to the user as the graph advances. Ordering here
# matches builder.py wiring (parse_brief → derive_persona → … → render_all).
# HITL nodes are absent because the runner replaces the status message with
# the dedicated text/image approve prompt instead.
_NODE_LABELS: dict[str, str] = {
    "parse_brief": "Разбираю бриф",
    "derive_persona": "Готовлю персон ЦА",
    "generate_message_candidates": "Генерирую варианты текста",
    "evaluate_as_persona_loop": "Оцениваю варианты глазами персон",
    "route_image_style": "Подбираю стиль картинки",
    "generate_image_prompt": "Пишу промпт для hero-картинки",
    "fill_templates_per_format": "Накладываю в шаблоны",
    "render_all": "Собираю ZIP",
}
_NODE_ORDER: list[str] = list(_NODE_LABELS.keys())


async def _ensure_progress_message(app: Application, session: Session) -> None:
    """Create the persistent 'Этап N/M: …' message on first use. We keep its
    message_id on the Session so resume calls can keep editing the same one
    instead of spamming new messages."""
    if session.progress_message_id is not None:
        return
    msg = await app.bot.send_message(
        chat_id=session.chat_id, text="Готовлюсь к генерации..."
    )
    session.progress_message_id = msg.message_id
    put(app.bot_data, session)


async def _edit_progress(app: Application, session: Session, node_name: str) -> None:
    """Edit the status message to reflect the freshly-completed node.
    Silently skips unknown nodes (HITL interrupts) and absorbs edit errors —
    progress UI is best-effort, must never break the run."""
    label = _NODE_LABELS.get(node_name)
    if label is None or session.progress_message_id is None:
        return
    try:
        idx = _NODE_ORDER.index(node_name) + 1
    except ValueError:
        return
    text = f"Этап {idx}/{len(_NODE_ORDER)}: {label}…"
    try:
        await app.bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.progress_message_id,
            text=text,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("progress_edit_failed", error=str(exc), node=node_name)


async def _run_graph_with_progress(
    app: Application, session: Session, payload, config: dict
) -> dict:
    """Stream the graph for one segment (initial run or resume) and feed each
    completed node into _edit_progress. Returns an accumulated 'final' dict
    that mirrors what ainvoke would have produced for the keys we care about
    in _handle_terminal_or_interrupt."""
    await _ensure_progress_message(app, session)
    final: dict = {}
    async for chunk in _graph(app).astream(payload, config=config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        if "__interrupt__" in chunk:
            final["__interrupt__"] = chunk["__interrupt__"]
            continue
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                final.update(update)
            await _edit_progress(app, session, node_name)
    return final


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
        final = await _run_graph_with_progress(app, session, initial, _config(session))
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
        final = await _run_graph_with_progress(
            app, session, Command(resume=decision), _config(session)
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
        if kind == "image_upload":
            await _render_image_upload(app, session, payload)
        else:
            await _render_text_approve(app, session, payload)
        return

    # terminal
    if final.get("cancelled"):
        _cancel_image_upload_timeout(app, session.thread_id)
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

    # Send the rendered PNGs as a media group first so the user gets an
    # in-chat preview, then deliver the same files bundled as a ZIP.
    # TG media group cap is 10 — batch if we ever cross that.
    try:
        for batch_start in range(0, len(files), 10):
            batch = files[batch_start : batch_start + 10]
            media: list[InputMediaPhoto] = []
            opened: list = []
            try:
                for i, rec in enumerate(batch):
                    fp = open(rec["path"], "rb")
                    opened.append(fp)
                    # Caption only on the first item of the first batch.
                    cap = caption if (batch_start == 0 and i == 0) else None
                    media.append(InputMediaPhoto(media=fp, caption=cap))
                await app.bot.send_media_group(
                    chat_id=session.chat_id, media=media
                )
            finally:
                for fp in opened:
                    fp.close()
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "media_group_send_failed", thread_id=session.thread_id, count=len(files)
        )
        await app.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"Превью картинок не отправилось ({type(exc).__name__}), "
                "ZIP с рендерами ниже."
            ),
        )

    try:
        with open(zip_path, "rb") as fh:
            await app.bot.send_document(
                chat_id=session.chat_id,
                document=fh,
                filename=zip_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                caption="Все ресайзы одним архивом.",
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


async def _render_image_upload(
    app: Application, session: Session, payload: dict
) -> None:
    """Show the EN image prompt to the user and wait for them to upload a hero.

    The user is expected to:
      - paste the prompt into their image generator (MJ / DALL-E / SDXL / Nano
        Banana / Flux),
      - send the resulting PNG back into the chat as a photo OR as an
        image-MIME Document (uncompressed preferred).

    A 24h timeout is scheduled — if no upload arrives the graph is resumed
    with action="timeout" and the session cancels itself.
    """
    image_prompt = payload.get("image_prompt") or ""
    image_style = payload.get("image_style") or "photo"

    instructions = (
        f"Стиль: {image_style}.\n"
        "Ниже — англоязычный prompt для hero-картинки. Скопируй его в свой "
        "генератор (MJ / DALL-E / SDXL / Flux / Nano Banana), сгенери и "
        "пришли результат сюда — фото или файл (Document) PNG.\n\n"
        "Окно загрузки: 24 часа. /cancel — отменить сессию."
    )
    # Code-block formatting makes the prompt one-tap-copyable in TG.
    prompt_block = f"```\n{image_prompt}\n```"
    text = f"{instructions}\n\n{prompt_block}"
    msg = await app.bot.send_message(
        chat_id=session.chat_id,
        text=text,
        parse_mode="Markdown",
    )
    session.hitl_message_id = msg.message_id
    session.status = "awaiting_image_upload"
    put(app.bot_data, session)

    # schedule 24h timeout
    _schedule_image_upload_timeout(app, session, image_style, image_prompt)


def _schedule_image_upload_timeout(
    app: Application, session: Session, image_style: str, image_prompt: str
) -> None:
    """Spawn a background sleeper that resumes the graph with action=timeout
    if the user hasn't uploaded anything within _IMAGE_UPLOAD_TIMEOUT_S."""
    store = app.bot_data.setdefault(_IMAGE_UPLOAD_TIMEOUTS_KEY, {})
    # cancel any previous timeout for this thread (paranoia — should not happen)
    old = store.pop(session.thread_id, None)
    if old is not None and not old.done():
        old.cancel()

    async def _timeout_coro() -> None:
        try:
            await asyncio.sleep(_IMAGE_UPLOAD_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        # If the session is still waiting, resume with timeout.
        current = get_active(app.bot_data, session.user_id)
        if current is None or current.thread_id != session.thread_id:
            return
        if current.status != "awaiting_image_upload":
            return
        log.warning("image_upload_timeout_fire", thread_id=session.thread_id)
        try:
            await app.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    "Время на загрузку картинки истекло (24 ч). "
                    "Сессия отменена — запусти /new когда будешь готов."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("timeout_notify_failed", error=str(exc))
        await _resume(app, current, {"action": "timeout"})

    task = asyncio.create_task(_timeout_coro())
    store[session.thread_id] = task


def _cancel_image_upload_timeout(app: Application, thread_id: str) -> None:
    store = app.bot_data.get(_IMAGE_UPLOAD_TIMEOUTS_KEY) or {}
    task = store.pop(thread_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _save_uploaded_hero(
    app: Application, session: Session, file_id: str, suffix: str
) -> Path | None:
    """Download a TG file by file_id and write it under _HEROES_DIR.

    Returns the resulting local Path on success, None on failure. Caller is
    expected to send an error message to the user on None.
    """
    try:
        _HEROES_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        out = _HEROES_DIR / f"{session.thread_id}_{ts}{suffix}"
        tg_file = await app.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(out))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "hero_download_failed",
            thread_id=session.thread_id,
            file_id=file_id,
            error=str(exc),
        )
        return None


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
        # Stale callback — drop the keyboard but keep the original message,
        # then notify in a separate message so the history isn't rewritten.
        await q.edit_message_reply_markup(reply_markup=None)
        if q.message is not None:
            await q.message.reply_text("Эта сессия больше не активна.")
        return

    # M3.3 testing: don't rewrite the HITL prompt — drop the keyboard
    # (so the user can't tap a second action) and post the decision /
    # next-step instruction as a fresh message.
    await q.edit_message_reply_markup(reply_markup=None)
    action = q.data.split(":", 1)[1]
    if action == "refine":
        session.status = "awaiting_refine"
        put(context.application.bot_data, session)
        if q.message is not None:
            await q.message.reply_text("Опиши одной-двумя фразами, что переделать.")
        return

    if q.message is not None:
        await q.message.reply_text(f"Выбрано: {action}. Возобновляю граф...")
    decision = {"action": action, "comment": None}
    context.application.create_task(_resume(context.application, session, decision))


async def on_image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photo / image-Document handler for the hero upload window.

    Acceptance:
      - update.message.photo (compressed; we take the largest size),
      - update.message.document with image/* MIME (uncompressed PNG/JPEG).

    Behavior:
      - Active session must be in status "awaiting_image_upload"; otherwise
        we silently ignore (the wizard or another stage may own the chat).
      - Latest-wins: if multiple photos arrive before resume, only the last
        one we successfully resume on counts; intermediate ones are processed
        and the graph resumes on the FIRST successful one, because as soon as
        the graph leaves the interrupt the session.status flips to "running".
        Anything sent after that is ignored.
    """
    msg = update.message
    if msg is None:
        return
    user = update.effective_user
    if user is None:
        return
    session = get_active(context.application.bot_data, user.id)
    if session is None or session.status != "awaiting_image_upload":
        return  # not for us

    file_id: str | None = None
    suffix = ".png"

    if msg.photo:
        # take the largest PhotoSize the client uploaded
        file_id = msg.photo[-1].file_id
        suffix = ".jpg"  # TG re-encodes compressed photos as JPEG
    elif msg.document is not None:
        mime = (msg.document.mime_type or "").lower()
        if not mime.startswith("image/"):
            await msg.reply_text(
                "Это не картинка. Пришли PNG/JPEG как фото или как Document с image/* MIME."
            )
            return
        file_id = msg.document.file_id
        # keep extension hint from document name if present
        name = (msg.document.file_name or "").lower()
        if name.endswith(".png"):
            suffix = ".png"
        elif name.endswith((".jpg", ".jpeg")):
            suffix = ".jpg"
        elif name.endswith(".webp"):
            suffix = ".webp"
    else:
        return  # neither photo nor doc — let other handlers see it

    # M3.3 testing: post each status as its own message instead of editing
    # the "Скачиваю..." notice in place — keeps the message log readable.
    await msg.reply_text("Скачиваю картинку...")

    path = await _save_uploaded_hero(
        context.application, session, file_id=file_id, suffix=suffix
    )
    if path is None:
        await msg.reply_text("Не удалось скачать файл. Попробуй ещё раз.")
        return

    # Mark the session as running BEFORE creating the resume task — this
    # prevents a second photo arriving in the same instant from triggering
    # a second resume (status check at the top of this handler will fail).
    session.status = "running"
    put(context.application.bot_data, session)
    _cancel_image_upload_timeout(context.application, session.thread_id)

    await msg.reply_text("Картинка принята. Накладываю в шаблоны...")

    decision = {
        "action": "upload",
        "local_path": str(path),
    }
    context.application.create_task(
        _resume(context.application, session, decision)
    )


async def on_refine_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    user = update.effective_user
    if user is None:
        return
    session = get_active(context.application.bot_data, user.id)
    if session is None or session.status != "awaiting_refine":
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
    # M3.3 testing: don't rewrite the [Сделать вариант B] button — drop the
    # keyboard and post status as a new message.
    await q.edit_message_reply_markup(reply_markup=None)
    reply = q.message.reply_text if q.message is not None else (
        lambda t: context.bot.send_message(chat_id=chat.id, text=t)
    )

    existing = get_active(context.application.bot_data, user.id)
    if existing is not None and existing.status not in {"done", "cancelled"}:
        await reply("Сначала заверши текущую сессию.")
        return

    _, prev_thread = q.data.split(":", 1)
    ab_store = context.application.bot_data.get("ab_prior", {})
    prior = ab_store.get(prev_thread)
    if prior is None:
        await reply("Данные A-варианта потеряны — запусти /new заново.")
        return

    # spawn a B-variant session reusing the same wizard_data — but raw_brief
    # is not stored on the user beyond wizard, so we just bump persona_priority
    # if there are >=2 personas. Image isn't built yet, so for M2 B-variant
    # only diversifies text axis.
    if existing is None:
        await reply(
            "Для варианта B нужен исходный бриф. Запусти /new и пройди мастер ещё раз — он подхватит prior_variant."
        )
        return
    await reply("M2: вариант B пока запускается только тем же /new — сохраню prior_variant в bot_data.")
    # full B-variant orchestration is M4 scope


def register_runner_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(on_hitl_callback, pattern=r"^hitl:(approve|regenerate|refine|cancel)$"))
    app.add_handler(CallbackQueryHandler(on_ab_callback, pattern=r"^ab:"))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_refine_text,
        ),
        group=1,  # runs after wizard MessageHandlers (group 0)
    )
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            on_image_upload,
        ),
        group=1,
    )
