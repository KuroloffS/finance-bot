import asyncio
import logging
import os

import uvicorn
from dotenv import load_dotenv
from telegram import BotCommand, MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import ApplicationBuilder

BOT_COMMANDS = [
    ("start", "Запустить бота и показать меню"),
    ("app", "📱 Открыть приложение"),
    ("analytics", "📊 Аналитика трат за месяц"),
    ("report", "📈 Подробный отчёт по категориям"),
    ("history", "📋 Последние траты"),
    ("goals", "🎯 Цели накопления"),
    ("debts", "🤝 Долги: кто кому должен"),
    ("tips", "💡 Советы по экономии"),
    ("budget", "💰 Месячный бюджет"),
    ("currency", "💱 Валюта"),
    ("settings", "🔔 Уведомления"),
    ("undo", "↩️ Удалить последнюю трату"),
    ("reset", "🗑 Удалить все траты"),
    ("lang", "🌐 Сменить язык (ru/en)"),
    ("help", "ℹ️ Как пользоваться ботом"),
]

BOT_DESCRIPTION = (
    "Личный финансовый советник 💸 Записывай траты текстом, голосом или фото чека — "
    "бот разложит по категориям, покажет аналитику и подскажет, где сэкономить. "
    "Открой приложение для красивой аналитики 📱"
)
BOT_SHORT_DESCRIPTION = "Учёт расходов с AI: текст, голос, фото чеков, аналитика и Mini App 💸"

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _version_tag() -> str:
    """Short deploy id — changes every Railway deploy, used to bust the
    Telegram Mini App webview cache."""
    return (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("RAILWAY_DEPLOYMENT_ID") or "").strip()[:8]


def webapp_url() -> str:
    url = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    if not url:
        dom = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if dom:
            url = "https://" + dom.rstrip("/")
    if url:
        tag = _version_tag()
        if tag:
            url += ("&" if "?" in url else "?") + "v=" + tag
    return url


async def run() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    port = int(os.getenv("PORT", 8000))

    application = ApplicationBuilder().token(token).build()

    from bot.handlers import register_handlers
    register_handlers(application)

    from services.scheduler_service import setup_scheduler
    scheduler = setup_scheduler(application)

    from web.app import create_app
    web_app = create_app()

    config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)

    async with application:
        await application.start()
        scheduler.start()
        logger.info("Scheduler started")

        try:
            await application.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
            await application.bot.set_my_description(BOT_DESCRIPTION)
            await application.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
            wu = webapp_url()
            if wu:
                await application.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="Open Dayon app", web_app=WebAppInfo(url=wu))
                )
                logger.info("Menu button → Mini App at %s", wu)
            else:
                logger.warning("WEBAPP_URL/RAILWAY_PUBLIC_DOMAIN not set — Mini App button skipped")
            logger.info("Bot commands & description set")
        except Exception as e:
            logger.warning("Failed to set bot metadata: %s", e)

        await application.updater.start_polling(
            drop_pending_updates=True, allowed_updates=Update.ALL_TYPES
        )
        logger.info("Bot polling + web server on port %s", port)

        try:
            await server.serve()
        finally:
            logger.info("Shutting down…")
            try:
                await application.updater.stop()
            except Exception:
                pass
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass
            await application.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
