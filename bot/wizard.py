"""5-step brief wizard.

Conversation states: PRODUCT -> GOAL -> AUDIENCE -> CHANNEL -> FORMATS -> CONFIRM.
Goals, channels, formats are inline keyboards (controlled vocabulary).
Product, audience, formats-extra are free-form text.

On confirm: serialize wizard_data to ``raw_brief`` (text dump fed to parse_brief
node) and hand off to graph_runner.start. Wizard exits the ConversationHandler.
"""

from __future__ import annotations

from typing import Any

import structlog
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.sessions import Session, drop, get_active, put

log = structlog.get_logger(__name__)

# --- states
PRODUCT, GOAL, AUDIENCE, CHANNEL, FORMATS, CONFIRM = range(6)

# --- controlled vocab
_GOALS = [
    ("awareness", "Знакомство"),
    ("consideration", "Рассмотрение"),
    ("conversion", "Конверсия"),
    ("engagement", "Вовлечение"),
    ("retention", "Удержание"),
]
_CHANNELS = [
    ("tg_post", "TG пост"),
    ("tg_story", "TG сторис"),
    ("vk_ad", "VK реклама"),
    ("ig_story", "IG сторис"),
    ("ig_post", "IG пост"),
    ("web_banner", "Веб-баннер"),
]
_DEFAULT_FORMATS = {
    "tg_post": ["tg_post_1080x1350"],
    "tg_story": ["tg_story_1080x1920"],
    "vk_ad": ["vk_ad_1080x1080"],
    "ig_story": ["ig_story_1080x1920"],
    "ig_post": ["ig_post_1080x1350"],
    "web_banner": ["web_banner_1200x628"],
}


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=t, callback_data=d) for d, t in row] for row in rows]
    )


# ----- entry & steps --------------------------------------------------------


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None or update.message is None:
        return ConversationHandler.END

    existing = get_active(context.application.bot_data, user.id)
    if existing is not None and existing.status not in {"done", "cancelled"}:
        await update.message.reply_text(
            "У тебя уже идёт активная сессия. Заверши её или отправь /cancel."
        )
        return ConversationHandler.END

    session = Session(user_id=user.id, chat_id=chat.id)
    put(context.application.bot_data, session)
    log.info("wizard_start", user_id=user.id, thread_id=session.thread_id)

    await update.message.reply_text(
        "Сессия запущена.\n\n"
        "Шаг 1/5. Что рекламируем? Опиши продукт в 1–2 предложениях."
    )
    return PRODUCT


async def on_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return PRODUCT
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["product"] = text

    kb = _kb([[(d, t)] for d, t in _GOALS])
    await update.message.reply_text(
        "Шаг 2/5. Цель кампании?", reply_markup=kb
    )
    return GOAL


async def on_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return GOAL
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["goal"] = q.data
    await q.edit_message_text(f"Цель: {_label(_GOALS, q.data)}")

    await context.bot.send_message(
        chat_id=session.chat_id,
        text="Шаг 3/5. Опиши целевую аудиторию: кто это, какие у них боли, чем мотивированы.",
    )
    return AUDIENCE


async def on_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return AUDIENCE
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["audience"] = text

    kb = _kb([[(d, t)] for d, t in _CHANNELS])
    await update.message.reply_text("Шаг 4/5. Канал размещения?", reply_markup=kb)
    return CHANNEL


async def on_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return CHANNEL
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["channel"] = q.data
    await q.edit_message_text(f"Канал: {_label(_CHANNELS, q.data)}")

    default_fmts = _DEFAULT_FORMATS.get(q.data, [])
    fmt_label = ", ".join(default_fmts) if default_fmts else "не определены"
    kb = _kb(
        [
            [("formats:default", f"Дефолт ({fmt_label})")],
            [("formats:custom", "Указать вручную")],
        ]
    )
    await context.bot.send_message(
        chat_id=session.chat_id,
        text="Шаг 5/5. Форматы выходных мастер-фреймов?",
        reply_markup=kb,
    )
    return FORMATS


async def on_formats_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return FORMATS
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END

    if q.data == "formats:default":
        session.wizard_data["formats"] = _DEFAULT_FORMATS.get(
            session.wizard_data.get("channel", ""), []
        )
        await q.edit_message_text(
            "Форматы: " + (", ".join(session.wizard_data["formats"]) or "не заданы")
        )
        return await _show_confirm(update, context, session)
    # custom
    await q.edit_message_text("Перечисли форматы через запятую (например tg_post_1080x1350, tg_story_1080x1920).")
    return FORMATS


async def on_formats_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return FORMATS
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    formats = [f.strip() for f in text.split(",") if f.strip()]
    session.wizard_data["formats"] = formats
    return await _show_confirm(update, context, session)


async def _show_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> int:
    summary = _render_brief_summary(session.wizard_data)
    kb = _kb(
        [
            [("confirm:run", "Запустить генерацию")],
            [("confirm:cancel", "Отмена")],
        ]
    )
    await context.bot.send_message(
        chat_id=session.chat_id,
        text=f"Бриф собран:\n\n{summary}",
        reply_markup=kb,
    )
    return CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return CONFIRM
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END

    if q.data == "confirm:cancel":
        await q.edit_message_text("Отменено.")
        drop(context.application.bot_data, user.id)
        return ConversationHandler.END

    # run
    await q.edit_message_text(
        "Бриф принят. Запускаю генерацию.\n"
        "Реальное время — 4–8 минут на компьютерные шаги плюс паузы на твои подтверждения "
        "(текст + картинка). Отдельным сообщением буду отмечать пройденные этапы."
    )
    session.status = "running"
    session.wizard_data["raw_brief"] = _render_raw_brief(session.wizard_data)

    # late import to avoid cycle
    from bot.graph_runner import start_session

    context.application.create_task(start_session(context.application, session))
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user is None:
        return ConversationHandler.END
    drop(context.application.bot_data, user.id)
    if update.message is not None:
        await update.message.reply_text("Сессия отменена.")
    return ConversationHandler.END


# ----- helpers --------------------------------------------------------------


def _label(vocab: list[tuple[str, str]], data: str) -> str:
    for d, t in vocab:
        if d == data:
            return t
    return data


def _render_brief_summary(d: dict[str, Any]) -> str:
    fmts = ", ".join(d.get("formats") or []) or "(не заданы)"
    return (
        f"Продукт: {d.get('product')}\n"
        f"Цель: {_label(_GOALS, d.get('goal', ''))}\n"
        f"ЦА: {d.get('audience')}\n"
        f"Канал: {_label(_CHANNELS, d.get('channel', ''))}\n"
        f"Форматы: {fmts}"
    )


def _render_raw_brief(d: dict[str, Any]) -> str:
    """Plain-text dump for parse_brief node."""
    return (
        f"Продукт: {d.get('product')}\n"
        f"Цель: {d.get('goal')}\n"
        f"ЦА: {d.get('audience')}\n"
        f"Канал: {d.get('channel')}\n"
        f"Форматы: {', '.join(d.get('formats') or [])}\n"
    )


# ----- ConversationHandler factory -----------------------------------------


def build_wizard_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("new", cmd_new)],
        states={
            PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_product)],
            GOAL: [CallbackQueryHandler(on_goal, pattern=r"^(awareness|consideration|conversion|engagement|retention)$")],
            AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_audience)],
            CHANNEL: [CallbackQueryHandler(on_channel, pattern=r"^(tg_post|tg_story|vk_ad|ig_story|ig_post|web_banner)$")],
            FORMATS: [
                CallbackQueryHandler(on_formats_choice, pattern=r"^formats:(default|custom)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_formats_text),
            ],
            CONFIRM: [CallbackQueryHandler(on_confirm, pattern=r"^confirm:(run|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="wizard",
        persistent=False,
    )
