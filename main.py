import logging
import os

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import ApplicationBuilder

BOT_COMMANDS = [
    ("start", "Запустить бота и показать меню"),
    ("analytics", "📊 Аналитика трат за месяц"),
    ("report", "📈 Подробный отчёт по категориям"),
    ("history", "📋 Последние траты"),
    ("tips", "💡 Советы по экономии"),
    ("budget", "💰 Месячный бюджет"),
    ("undo", "↩️ Удалить последнюю трату"),
    ("reset", "🗑 Удалить все траты"),
    ("lang", "🌐 Сменить язык (ru/en)"),
    ("help", "ℹ️ Как пользоваться ботом"),
]

BOT_DESCRIPTION = (
    "Личный финансовый советник 💸 Записывай траты текстом, голосом или фото чека — "
    "бот разложит по категориям, покажет аналитику и подскажет, где сэкономить."
)
BOT_SHORT_DESCRIPTION = "Учёт расходов с AI: текст, голос, фото чеков, аналитика и советы 💸"

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    webhook_url = os.getenv("WEBHOOK_URL", "")
    port = int(os.getenv("PORT", 8000))

    application = ApplicationBuilder().token(token).build()

    from bot.handlers import register_handlers
    register_handlers(application)

    from services.scheduler_service import setup_scheduler
    scheduler = setup_scheduler(application)

    async def on_startup(app):
        scheduler.start()
        logger.info("Scheduler started")
        try:
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
            await app.bot.set_my_description(BOT_DESCRIPTION)
            await app.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
            logger.info("Bot commands & description set")
        except Exception as e:
            logger.warning("Failed to set bot commands/description: %s", e)

    async def on_shutdown(app):
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    application.post_init = on_startup
    application.post_shutdown = on_shutdown

    # allowed_updates=ALL_TYPES is required so inline button presses (callback_query)
    # are delivered — without it /reset and 🗑 delete buttons never reach the bot.
    webhook_url = webhook_url.strip().rstrip("/")
    if webhook_url:
        logger.info("Starting webhook on port %s → %s/webhook", port, webhook_url)
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=f"{webhook_url}/webhook",
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Starting bot in polling mode (no WEBHOOK_URL set)")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )


if __name__ == "__main__":
    main()
