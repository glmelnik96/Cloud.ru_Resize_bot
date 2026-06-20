"""Entry point. PTB 22.x async, whitelist middleware, /start wizard stub.

M0 scope: bot starts, whitelisted users get /start with intro message.
Wizard, LangGraph orchestration, HITL — added in M1+.
"""

from __future__ import annotations

import structlog
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

from bot.sessions import drop, get_active

from bot.config import get_settings
from bot.logging_config import configure_logging

load_dotenv()
configure_logging()
log = structlog.get_logger(__name__)


async def whitelist_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop updates from non-whitelisted users. Group -1 runs before other handlers.

    Exception: when USE_PHYGITAL_RENDER is on, inbound messages from
    @{PHYGITAL_BOT_USERNAME} (another bot) are allowed through — they carry
    the @b2b OK/ERROR replies for our outstanding render requests. We do not
    auto-reply to them: their handler raises ApplicationHandlerStop after
    processing so they never reach wizard/menu logic.
    """
    settings = get_settings()
    user = update.effective_user
    log.info("update_received", update_id=update.update_id, user_id=user.id if user else None,
             text=(update.message.text if update.message else None))
    if user is None:
        raise ApplicationHandlerStop
    if user.id not in settings.whitelist_user_ids:
        if (
            settings.use_phygital_render
            and user.is_bot
            and (user.username or "").lower() == settings.phygital_bot_username.lower()
        ):
            return  # allow phygital bot's replies through
        log.warning("whitelist_blocked", user_id=user.id, username=user.username)
        if update.message is not None:
            await update.message.reply_text(
                "Доступ ограничен. Свяжитесь с администратором."
            )
        raise ApplicationHandlerStop


_MENU_TEXT = (
    "Resize_bot — генерация рекламных креативов под бренд Cloud.ru.\n"
    "\n"
    "Команды:\n"
    "/new — новый бриф (3 шага: продукт, цель, аудитория) и запуск генерации\n"
    "/status — что сейчас происходит с моей сессией\n"
    "/cancel — отменить текущую сессию\n"
    "/help — показать это меню\n"
    "/ping — проверить связь с Cloud.ru FM (диагностика)\n"
    "\n"
    "Что бот делает:\n"
    "1. Собирает бриф (3 шага в чате).\n"
    "2. Сам пишет slogan/body/CTA, прогоняет через симуляцию целевой аудитории.\n"
    "3. Просит загрузить hero-картинку (или сгенерит EN-промпт для генератора).\n"
    "4. Собирает 3 финальных баннера (240x400, 300x250, 300x500) и присылает ZIP."
)


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    user = update.effective_user
    log.info("cmd_start", user_id=user.id if user else None)
    await update.message.reply_text(_MENU_TEXT)


async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(_MENU_TEXT)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    session = get_active(context.application.bot_data, update.effective_user.id)
    if session is None:
        await update.message.reply_text(
            "Активной сессии нет. /new чтобы начать."
        )
        return
    await update.message.reply_text(
        f"Сессия активна.\n"
        f"Статус: {session.status}\n"
        f"thread_id: {session.thread_id[:8]}…\n"
        f"/cancel — отменить."
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global /cancel — fires regardless of wizard / running / awaiting_* state.

    The wizard's own ConversationHandler also has a /cancel fallback, but it
    only matches while the conversation is open. After wizard.END (i.e. the
    graph is running, or we're stuck on awaiting_image_upload / awaiting_hitl),
    that fallback never fires. This handler is registered at app level so
    /cancel works from *any* state.

    Cleanup performed:
      - drop the per-user session
      - cancel any image_upload timeout task
      - resolve any pending b2b Future with a cancellation error so the
        _request_phygital_render coroutine unblocks immediately instead of
        sitting on its 10-min wait_for
    """
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    session = get_active(context.application.bot_data, user_id)
    if session is None:
        await update.message.reply_text("Активной сессии нет.")
        return

    # Cancel image_upload timeout if armed.
    from bot.graph_runner import (
        B2B_PENDING_KEY,
        B2BError,
        _cancel_image_upload_timeout,
        cancel_hero_prefetch,
    )

    _cancel_image_upload_timeout(context.application, session.thread_id)
    cancel_hero_prefetch(context.application, session.thread_id)

    # Wake any waiting b2b request for this thread.
    pending = context.application.bot_data.get(B2B_PENDING_KEY) or {}
    for corr, entry in list(pending.items()):
        if entry.get("thread_id") != session.thread_id:
            continue
        fut = entry.get("future")
        if fut is not None and not fut.done():
            fut.set_exception(B2BError("user_cancelled"))
        pending.pop(corr, None)

    drop(context.application.bot_data, user_id)
    log.info("session_cancelled", user_id=user_id, thread_id=session.thread_id)
    await update.message.reply_text("Сессия отменена.")


async def cmd_ping(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    from llm.cloudru import ModelCall, ModelName, get_shared_client

    client = get_shared_client()
    await update.message.reply_text("Пингую Cloud.ru FM...")
    results: list[str] = []
    for model in (ModelName.GLM, ModelName.DEEPSEEK, ModelName.KIMI):
        try:
            content = await client.call(
                ModelCall(
                    model=model,
                    messages=[
                        {"role": "user", "content": "Ответь одним словом: ok"},
                    ],
                    thinking=False,
                    max_tokens=20 if model != ModelName.KIMI else 2500,
                )
            )
            results.append(f"{model.value}: ok ({content.strip()[:30]})")
        except Exception as exc:  # noqa: BLE001
            log.exception("ping_failed", model=model.value)
            results.append(f"{model.value}: FAIL ({type(exc).__name__})")
    await update.message.reply_text("\n".join(results))


async def _post_init(app: Application) -> None:
    """M3.3 boot — no Phygital, no Figma MCP, no tunnel, no static server.

    The hero PNG comes from the user via TG upload (hitl_image_upload), and
    final rendering is local PIL composition (no remote callbacks). The only
    background task left is the TTL janitor for /data/heroes|renders|zips.
    """
    from bot.graph_runner import init_compiled_graph
    from infra.admin_alert import set_admin_notifier
    from infra.ttl_janitor import start_ttl_janitor

    set_admin_notifier(app.bot, get_settings().admin_user_id)
    app.bot_data["_ttl_janitor_task"] = start_ttl_janitor()

    # Register the clickable "Menu" in Telegram UI. PTB sends these to the
    # `setMyCommands` endpoint; the user sees them in the blue "/" button.
    await app.bot.set_my_commands(
        [
            BotCommand("new", "Новый бриф и запуск генерации"),
            BotCommand("status", "Что сейчас происходит с моей сессией"),
            BotCommand("cancel", "Отменить текущую сессию"),
            BotCommand("help", "Показать меню и описание"),
            BotCommand("ping", "Диагностика связи с Cloud.ru FM"),
        ]
    )

    await init_compiled_graph(app)


async def _post_shutdown(app: Application) -> None:
    from bot.graph_runner import shutdown_compiled_graph
    from infra.admin_alert import clear_admin_notifier
    from infra.ttl_janitor import stop_ttl_janitor

    await shutdown_compiled_graph(app)
    await stop_ttl_janitor()
    app.bot_data.pop("_ttl_janitor_task", None)
    clear_admin_notifier()


def build_application() -> Application:
    # /banner scenario pipeline is archived — code kept in bot/banner_wizard.py
    # and bot/banner_runner.py but not registered. Re-enable by importing
    # build_banner_handler and adding it back below + to the menu/commands.
    from bot.graph_runner import register_runner_handlers
    from bot.wizard import build_wizard_handler

    settings = get_settings()
    if not settings.whitelist_user_ids:
        log.warning("empty_whitelist", note="WHITELIST_USER_IDS not set, bot will reject everyone")
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(TypeHandler(Update, whitelist_gate), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ping", cmd_ping))
    # Global /cancel must be registered BEFORE the wizard handler so it wins
    # the dispatch race when wizard is still active. Outside the wizard,
    # there's no other competitor — this is the handler that makes /cancel
    # work in any state (running, awaiting_image_upload, awaiting_hitl, etc).
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(build_wizard_handler())
    register_runner_handlers(app)
    app.add_error_handler(_on_error)
    return app


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler_error", error=str(context.error))


def main() -> None:
    log.info(
        "boot",
        admin=get_settings().admin_user_id,
        whitelist_size=len(get_settings().whitelist_user_ids),
    )
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
