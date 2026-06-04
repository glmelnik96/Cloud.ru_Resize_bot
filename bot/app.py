"""Entry point. PTB 22.x async, whitelist middleware, /start wizard stub.

M0 scope: bot starts, whitelisted users get /start with intro message.
Wizard, LangGraph orchestration, HITL — added in M1+.
"""

from __future__ import annotations

import structlog
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

from bot.config import get_settings
from bot.logging_config import configure_logging

load_dotenv()
configure_logging()
log = structlog.get_logger(__name__)


async def whitelist_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop updates from non-whitelisted users. Group 0 runs before other handlers."""
    settings = get_settings()
    user = update.effective_user
    log.info("update_received", update_id=update.update_id, user_id=user.id if user else None,
             text=(update.message.text if update.message else None))
    if user is None:
        raise ApplicationHandlerStop
    if user.id not in settings.whitelist_user_ids:
        log.warning("whitelist_blocked", user_id=user.id, username=user.username)
        if update.message is not None:
            await update.message.reply_text(
                "Доступ ограничен. Свяжитесь с администратором."
            )
        raise ApplicationHandlerStop


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    user = update.effective_user
    log.info("cmd_start", user_id=user.id if user else None)
    await update.message.reply_text(
        "Resize_bot готов.\n\n"
        "Команды:\n"
        "/new — собрать бриф и запустить генерацию\n"
        "/cancel — отменить текущую сессию\n"
        "/ping — проверить связь с Cloud.ru FM"
    )


async def cmd_ping(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    from llm.cloudru import CloudRuClient, ModelCall, ModelName

    client = CloudRuClient()
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
    from bot.graph_runner import init_compiled_graph
    from infra.admin_alert import set_admin_notifier
    from infra.figma_mcp import start_figma_mcp_client
    from infra.http_server import start_static_server
    from infra.phygital_client import start_phygital_client
    from infra.ttl_janitor import start_ttl_janitor
    from infra.tunnel import start_tunnel

    set_admin_notifier(app.bot, get_settings().admin_user_id)
    app.bot_data["_ttl_janitor_task"] = start_ttl_janitor()

    server, thread, port = start_static_server()
    app.bot_data["_http_server"] = server
    app.bot_data["_http_thread"] = thread
    app.bot_data["_http_port"] = port

    try:
        handle = await start_tunnel(port)
        app.bot_data["_tunnel_handle"] = handle
        app.bot_data["public_base_url"] = handle.public_url
        log.info(
            "infra_ready",
            http_port=port,
            tunnel_mode=handle.mode,
            public_base_url=handle.public_url or "(none)",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("tunnel_start_failed", error=str(exc))
        app.bot_data["public_base_url"] = ""

    # Phygital+ brand text→image client (M3.1). Returns None if disabled or
    # session.json missing — generate_image then falls back to PIL stub.
    phygital = await start_phygital_client()
    app.bot_data["phygital_enabled"] = phygital is not None

    figma = await start_figma_mcp_client()
    app.bot_data["figma_mcp_enabled"] = figma is not None

    await init_compiled_graph(app)


async def _post_shutdown(app: Application) -> None:
    from bot.graph_runner import shutdown_compiled_graph
    from infra.admin_alert import clear_admin_notifier
    from infra.figma_mcp import stop_figma_mcp_client
    from infra.http_server import stop_static_server
    from infra.phygital_client import stop_phygital_client
    from infra.ttl_janitor import stop_ttl_janitor
    from infra.tunnel import stop_tunnel

    await shutdown_compiled_graph(app)
    await stop_ttl_janitor()
    app.bot_data.pop("_ttl_janitor_task", None)
    await stop_phygital_client()
    await stop_figma_mcp_client()
    clear_admin_notifier()

    handle = app.bot_data.pop("_tunnel_handle", None)
    if handle is not None:
        await stop_tunnel(handle)

    server = app.bot_data.pop("_http_server", None)
    if server is not None:
        stop_static_server(server)
    app.bot_data.pop("_http_thread", None)


def build_application() -> Application:
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
    app.add_handler(CommandHandler("ping", cmd_ping))
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
