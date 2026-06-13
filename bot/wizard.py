"""3-step brief wizard.

Conversation states: PRODUCT -> GOAL -> AUDIENCE -> CONFIRM.
Goal is an inline keyboard (controlled vocabulary). Product and audience
are free-form text.

Channel is NOT asked explicitly — parse_brief extracts it from the audience
free-text (if the marketer mentions "ЦА в VK / TG / IG") and defaults to
``tg_post`` otherwise (M3.3: one less click in the wizard).

Formats are NOT asked either — they are hard-wired to the full slug
whitelist from ``config/templates.json`` (M3.3: every brief produces all
banners the composer knows how to render).

On confirm: serialize wizard_data to ``raw_brief`` (text dump fed to parse_brief
node) and hand off to graph_runner.start. Wizard exits the ConversationHandler.
"""

from __future__ import annotations

from pathlib import Path
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
from infra.template_manifest import load_manifest

log = structlog.get_logger(__name__)

# --- states
PRODUCT, GOAL, AUDIENCE, CONFIRM = range(4)

# --- controlled vocab
_GOALS = [
    ("awareness", "Знакомство"),
    ("consideration", "Рассмотрение"),
    ("conversion", "Конверсия"),
    ("engagement", "Вовлечение"),
    ("retention", "Удержание"),
]

# --- formats: hard-wired to manifest slugs (M3.3)
_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "config" / "templates.json"
_MANIFEST_SLUGS_CACHE: list[str] | None = None


def _manifest_slugs() -> list[str]:
    """Load manifest slugs once per process. Cached because the manifest
    doesn't change at runtime — a code change to templates.json requires a
    container rebuild anyway."""
    global _MANIFEST_SLUGS_CACHE
    if _MANIFEST_SLUGS_CACHE is None:
        _MANIFEST_SLUGS_CACHE = list(load_manifest(_MANIFEST_PATH).templates.keys())
    return _MANIFEST_SLUGS_CACHE


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=t, callback_data=d) for d, t in row] for row in rows]
    )


def _brief_session(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Session | None:
    """Active session iff it belongs to the /new (brief) pipeline. Returns None
    for a /banner session so a lingering brief ConversationHandler never writes
    into the banner flow (and vice-versa)."""
    s = get_active(context.application.bot_data, user_id)
    if s is None or s.kind != "brief":
        return None
    return s


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
        "Шаг 1/3. Что рекламируем? Опиши продукт в 1–2 предложениях."
    )
    return PRODUCT


async def on_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return PRODUCT
    user = update.effective_user
    session = _brief_session(context, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["product"] = text

    kb = _kb([[(d, t)] for d, t in _GOALS])
    await update.message.reply_text(
        "Шаг 2/3. Цель кампании?", reply_markup=kb
    )
    return GOAL


async def on_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return GOAL
    await q.answer()
    user = update.effective_user
    session = _brief_session(context, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["goal"] = q.data
    # M3.3 testing: don't rewrite the question — drop the keyboard and append
    # the chosen value as a new message so the wizard history stays readable.
    await q.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(
        chat_id=session.chat_id,
        text=f"Выбрано: {_label(_GOALS, q.data)}",
    )

    await context.bot.send_message(
        chat_id=session.chat_id,
        text=(
            "Шаг 3/3. Опиши целевую аудиторию: кто это, какие у них боли, чем мотивированы. "
            "Если знаешь канал размещения (TG / VK / IG / web) — упомяни его здесь же."
        ),
    )
    return AUDIENCE


async def on_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return AUDIENCE
    user = update.effective_user
    session = _brief_session(context, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["audience"] = text

    # M3.3: formats are hard-wired to the full manifest slug list — every
    # brief renders all known banners. No question for the marketer.
    # Channel is inferred by parse_brief from the audience free-text
    # (default tg_post if not mentioned) — no explicit step either.
    session.wizard_data["formats"] = _manifest_slugs()
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
    session = _brief_session(context, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END

    if q.data == "confirm:cancel":
        # M3.3 testing: keep the original brief summary visible, just drop the
        # keyboard and append the decision as a new message.
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=session.chat_id, text="Отменено.")
        drop(context.application.bot_data, user.id)
        return ConversationHandler.END

    # run
    await q.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(
        chat_id=session.chat_id,
        text=(
            "Бриф принят. Запускаю генерацию.\n"
            "Реальное время — 4–8 минут на компьютерные шаги плюс паузы на твои подтверждения "
            "(текст + картинка). Отдельным сообщением буду отмечать пройденные этапы."
        ),
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
        f"Форматы: {fmts}"
    )


def _render_raw_brief(d: dict[str, Any]) -> str:
    """Plain-text dump for parse_brief node.

    Channel is intentionally not surfaced here — parse_brief infers it from
    the audience free-text (or defaults to tg_post). See parse_brief.md."""
    return (
        f"Продукт: {d.get('product')}\n"
        f"Цель: {d.get('goal')}\n"
        f"ЦА: {d.get('audience')}\n"
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
            CONFIRM: [CallbackQueryHandler(on_confirm, pattern=r"^confirm:(run|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="wizard",
        persistent=False,
    )
