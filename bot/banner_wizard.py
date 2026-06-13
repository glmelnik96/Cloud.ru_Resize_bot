"""/banner wizard: scenario menu -> slot texts -> hero -> render.

No LLM. The user picks a scenario (and, for TG covers, an archetype), types
the slot texts, then either uploads a hero image or asks Phygital to generate
one. On confirm we hand off to bot.banner_runner.run_banner which removes the
background, renders every format of the scenario and ships a ZIP.

Single active session per user (shared store with /new) — entry is blocked if
another session is mid-flight.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import get_settings
from bot.sessions import Session, drop, get_active, put
from infra.banner_render import load_banner_manifest
from infra.template_manifest import HeroCutoutLayer

log = structlog.get_logger(__name__)

# states
B_SCENARIO, B_ARCHETYPE, B_SLOT, B_HERO, B_HERO_PROMPT, B_CONFIRM = range(6)

_HEROES_DIR = Path("/data/heroes")

# Scenario buttons shown on /banner. Order = display order.
_SCENARIOS = [
    ("webinar_visual", "Вебинар с вижуалом"),
    ("webinar_speaker", "Вебинар со спикером"),
    ("smm_covers", "ТГ-обложка"),
]

# SMM archetypes (each is one format of the smm_covers scenario).
_ARCHETYPES = [
    ("smm_cover_visual", "Визуал"),
    ("smm_cover_speaker", "Спикер"),
    ("smm_cover_announce", "Анонс вебинара"),
    ("smm_cover_text", "Текст"),
]

# Human prompts for each collectable slot (derived slots datetime/speaker are
# computed by the renderer and never asked).
_SLOT_PROMPTS = {
    "title": "Заголовок",
    "subtitle": "Подзаголовок",
    "date": "Дата (например: 2 июля)",
    "time": "Время (например: 11:00)",
    "speaker_name": "Имя спикера",
    "speaker_role": "Должность спикера",
}

# Which slots to ask per scenario (webinars) or per SMM archetype format.
_ASK = {
    "webinar_visual": ["title", "date", "time"],
    "webinar_speaker": ["title", "date", "time", "speaker_name", "speaker_role"],
    "smm_cover_visual": ["title", "subtitle"],
    "smm_cover_speaker": ["title", "speaker_name", "speaker_role"],
    "smm_cover_announce": ["title", "date", "time", "speaker_name", "speaker_role"],
    "smm_cover_text": ["title"],
}


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=t, callback_data=d) for d, t in row] for row in rows]
    )


def _ask_key(scenario_id: str, formats: list[str] | None) -> str:
    """Key into _ASK: SMM archetypes vary per chosen format, webinars per scenario."""
    if scenario_id == "smm_covers" and formats:
        return formats[0]
    return scenario_id


def _scenario_needs_hero(scenario_id: str, formats: list[str] | None) -> bool:
    """True if any rendered format has a hero slot (cutout OR framed cover)."""
    manifest = load_banner_manifest()
    fmts = formats or manifest.scenarios[scenario_id].formats
    for f in fmts:
        if any(l.type in ("hero", "hero_cutout") for l in manifest.templates[f].layers):
            return True
    return False


# Hero source override per SMM archetype: speaker faces are real people — never
# generated, upload only. Webinars / visual covers fall back to the scenario's
# HeroPolicy.source. Keys are format slugs (SMM archetypes).
_HERO_SOURCE_OVERRIDE = {
    "smm_cover_speaker": "upload",
    "smm_cover_announce": "upload",
    "smm_cover_visual": "both",
}


def _hero_source(scenario_id: str, formats: list[str] | None) -> str:
    """Resolve the hero source (generate | upload | both) for this run."""
    if scenario_id == "smm_covers" and formats:
        override = _HERO_SOURCE_OVERRIDE.get(formats[0])
        if override:
            return override
    manifest = load_banner_manifest()
    policy = manifest.scenarios[scenario_id].hero
    return policy.source if policy else "upload"


# ----- entry & steps --------------------------------------------------------


async def cmd_banner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

    session = Session(user_id=user.id, chat_id=chat.id, status="banner_wizard")
    put(context.application.bot_data, session)
    log.info("banner_wizard_start", user_id=user.id, thread_id=session.thread_id)
    await update.message.reply_text(
        "Сценарные баннеры. Выбери сценарий:",
        reply_markup=_kb([[(f"bsc:{sid}", title)] for sid, title in _SCENARIOS]),
    )
    return B_SCENARIO


async def on_scenario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None or q.data is None:
        return B_SCENARIO
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    scenario_id = q.data.split(":", 1)[1]
    session.wizard_data["scenario_id"] = scenario_id
    await q.edit_message_reply_markup(reply_markup=None)

    if scenario_id == "smm_covers":
        await context.bot.send_message(
            chat_id=session.chat_id,
            text="Выбери тип обложки:",
            reply_markup=_kb([[(f"barch:{fid}", t)] for fid, t in _ARCHETYPES]),
        )
        return B_ARCHETYPE

    session.wizard_data["formats"] = None
    return await _begin_slots(context, session)


async def on_archetype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None or q.data is None:
        return B_ARCHETYPE
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    fmt = q.data.split(":", 1)[1]
    session.wizard_data["formats"] = [fmt]
    await q.edit_message_reply_markup(reply_markup=None)
    return await _begin_slots(context, session)


async def _begin_slots(context: ContextTypes.DEFAULT_TYPE, session: Session) -> int:
    scenario_id = session.wizard_data["scenario_id"]
    formats = session.wizard_data.get("formats")
    queue = list(_ASK[_ask_key(scenario_id, formats)])
    session.wizard_data["ask_queue"] = queue
    session.wizard_data["texts"] = {}
    put(context.application.bot_data, session)
    await context.bot.send_message(
        chat_id=session.chat_id, text=f"{_SLOT_PROMPTS[queue[0]]}:"
    )
    return B_SLOT


async def on_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return B_SLOT
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    queue: list[str] = session.wizard_data["ask_queue"]
    current = queue.pop(0)
    session.wizard_data["texts"][current] = text
    if queue:
        put(context.application.bot_data, session)
        await update.message.reply_text(f"{_SLOT_PROMPTS[queue[0]]}:")
        return B_SLOT

    # texts done → hero or confirm
    scenario_id = session.wizard_data["scenario_id"]
    formats = session.wizard_data.get("formats")
    if not _scenario_needs_hero(scenario_id, formats):
        return await _show_confirm(context, session)

    source = _hero_source(scenario_id, formats)
    put(context.application.bot_data, session)
    if source in ("generate", "both"):
        await update.message.reply_text(
            "Картинка: загрузить свою или сгенерировать?",
            reply_markup=_kb([[("bhero:upload", "Загрузить"), ("bhero:generate", "Сгенерировать")]]),
        )
    else:
        await update.message.reply_text(
            "Пришли изображение спикера/героя — фото или файл (PNG/JPEG). "
            "Фон уберу автоматически."
        )
    return B_HERO


async def on_hero_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None or q.data is None:
        return B_HERO
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    choice = q.data.split(":", 1)[1]
    await q.edit_message_reply_markup(reply_markup=None)
    if choice == "generate":
        return await _propose_prompt(context, session)
    await context.bot.send_message(
        chat_id=session.chat_id,
        text="Пришли изображение — фото или файл (PNG/JPEG). Фон уберу автоматически.",
    )
    return B_HERO


async def _propose_prompt(context: ContextTypes.DEFAULT_TYPE, session: Session) -> int:
    """Auto-write an EN image prompt from the banner title, show it for the user
    to confirm or edit."""
    from infra.banner_image_prompt import generate_banner_image_prompt

    title = session.wizard_data.get("texts", {}).get("title", "").strip()
    if not title:
        await context.bot.send_message(
            chat_id=session.chat_id,
            text="Опиши картинку для генерации (можно по-английски). Без текста и логотипов.",
        )
        return B_HERO_PROMPT

    cutout = _scenario_is_cutout(session)
    await context.bot.send_message(chat_id=session.chat_id, text="Придумываю промпт по теме…")
    try:
        prompt = await generate_banner_image_prompt(title, cutout=cutout)
    except Exception as exc:  # noqa: BLE001
        log.warning("banner_prompt_gen_failed", thread_id=session.thread_id, error=str(exc))
        await context.bot.send_message(
            chat_id=session.chat_id,
            text="Не получилось придумать промпт. Напиши его сам (можно по-английски):",
        )
        return B_HERO_PROMPT

    session.wizard_data["gen_prompt"] = prompt
    put(context.application.bot_data, session)
    await context.bot.send_message(
        chat_id=session.chat_id,
        text=(
            "Предлагаю такой промпт для картинки:\n\n"
            f"{prompt}\n\n"
            "Можешь прислать свой вариант текстом, либо подтвердить."
        ),
        reply_markup=_kb([[("bprompt:ok", "Сгенерировать с этим"), ("bprompt:cancel", "Отмена")]]),
    )
    return B_HERO_PROMPT


def _scenario_is_cutout(session: Session) -> bool:
    """True if the chosen format(s) use a hero_cutout (→ generate with rmbg)."""
    manifest = load_banner_manifest()
    wd = session.wizard_data
    fmts = wd.get("formats") or manifest.scenarios[wd["scenario_id"]].formats
    for f in fmts:
        if any(isinstance(l, HeroCutoutLayer) for l in manifest.templates[f].layers):
            return True
    return False


async def on_prompt_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None or q.data is None:
        return B_HERO_PROMPT
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    await q.edit_message_reply_markup(reply_markup=None)
    if q.data.endswith(":cancel"):
        drop(context.application.bot_data, user.id)
        await context.bot.send_message(chat_id=session.chat_id, text="Отменено.")
        return ConversationHandler.END
    # ok → prompt already stored in wizard_data
    return await _show_confirm(context, session)


async def on_hero_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if not text:
        return B_HERO_PROMPT
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    session.wizard_data["gen_prompt"] = text
    put(context.application.bot_data, session)
    return await _show_confirm(context, session)


async def on_hero_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if msg is None:
        return B_HERO
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END

    file_id: str | None = None
    suffix = ".png"
    if msg.photo:
        file_id = msg.photo[-1].file_id
        suffix = ".jpg"
    elif msg.document is not None and (msg.document.mime_type or "").lower().startswith("image/"):
        file_id = msg.document.file_id
        name = (msg.document.file_name or "").lower()
        suffix = ".png" if name.endswith(".png") else (".jpg" if name.endswith((".jpg", ".jpeg")) else ".png")
    else:
        await msg.reply_text("Нужна картинка — фото или файл с image/* MIME.")
        return B_HERO

    await msg.reply_text("Скачиваю картинку…")
    try:
        _HEROES_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        out = _HEROES_DIR / f"{session.thread_id}_{ts}{suffix}"
        tg_file = await context.application.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(out))
    except Exception as exc:  # noqa: BLE001
        log.exception("banner_hero_download_failed", thread_id=session.thread_id)
        await msg.reply_text(f"Не удалось скачать ({type(exc).__name__}). Пришли ещё раз.")
        return B_HERO
    session.wizard_data["hero_path"] = str(out)
    put(context.application.bot_data, session)
    return await _show_confirm(context, session)


async def _show_confirm(context: ContextTypes.DEFAULT_TYPE, session: Session) -> int:
    wd = session.wizard_data
    manifest = load_banner_manifest()
    scenario = manifest.scenarios[wd["scenario_id"]]
    n_formats = len(wd.get("formats") or scenario.formats)
    lines = [f"Сценарий: {scenario.title}", f"Форматов: {n_formats}"]
    for k, v in wd.get("texts", {}).items():
        lines.append(f"{_SLOT_PROMPTS.get(k, k)}: {v}")
    if wd.get("gen_prompt"):
        lines.append("Картинка: генерация по промпту")
    elif wd.get("hero_path"):
        lines.append("Картинка: загружена")
    else:
        lines.append("Картинка: без героя")
    await context.bot.send_message(
        chat_id=session.chat_id,
        text="Проверь:\n\n" + "\n".join(lines),
        reply_markup=_kb([[("bconf:run", "Запустить"), ("bconf:cancel", "Отмена")]]),
    )
    return B_CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None or q.data is None:
        return B_CONFIRM
    await q.answer()
    user = update.effective_user
    session = get_active(context.application.bot_data, user.id)  # type: ignore[union-attr]
    if session is None:
        return ConversationHandler.END
    await q.edit_message_reply_markup(reply_markup=None)
    if q.data.endswith(":cancel"):
        drop(context.application.bot_data, user.id)
        await context.bot.send_message(chat_id=session.chat_id, text="Отменено.")
        return ConversationHandler.END

    session.status = "running"
    put(context.application.bot_data, session)
    await context.bot.send_message(
        chat_id=session.chat_id, text="Принято. Рендерю форматы…"
    )
    from bot.banner_runner import run_banner

    context.application.create_task(run_banner(context.application, session))
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user is not None:
        drop(context.application.bot_data, user.id)
    if update.message is not None:
        await update.message.reply_text("Сессия отменена.")
    return ConversationHandler.END


def build_banner_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("banner", cmd_banner)],
        states={
            B_SCENARIO: [CallbackQueryHandler(on_scenario, pattern=r"^bsc:")],
            B_ARCHETYPE: [CallbackQueryHandler(on_archetype, pattern=r"^barch:")],
            B_SLOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_slot)],
            B_HERO: [
                CallbackQueryHandler(on_hero_choice, pattern=r"^bhero:"),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_hero_upload),
            ],
            B_HERO_PROMPT: [
                CallbackQueryHandler(on_prompt_decision, pattern=r"^bprompt:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_hero_prompt),
            ],
            B_CONFIRM: [CallbackQueryHandler(on_confirm, pattern=r"^bconf:")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="banner_wizard",
        persistent=False,
    )
