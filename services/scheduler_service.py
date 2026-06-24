import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.supabase_service import get_all_users, get_monthly_summary, get_budget_status
from utils.formatters import format_monthly_report

logger = logging.getLogger(__name__)


def setup_scheduler(application) -> AsyncIOScheduler:
    tz = pytz.timezone("Asia/Tashkent")
    scheduler = AsyncIOScheduler(timezone=tz)

    async def send_monthly_reports():
        logger.info("Running monthly reports scheduler job")
        users = await get_all_users()
        for user in users:
            try:
                tid = user["telegram_id"]
                lang = user.get("language", "ru")
                summary = await get_monthly_summary(tid)
                status = await get_budget_status(tid)
                if not summary:
                    continue
                text = format_monthly_report(summary, status, lang)
                await application.bot.send_message(
                    chat_id=tid,
                    text=text,
                    parse_mode="HTML",
                )
                logger.info("Monthly report sent to %s", tid)
            except Exception as e:
                logger.warning(
                    "Failed to send monthly report to %s: %s",
                    user.get("telegram_id"),
                    e,
                )

    scheduler.add_job(
        send_monthly_reports,
        trigger=CronTrigger(day="last", hour=18, minute=0),
        id="monthly_reports",
        replace_existing=True,
    )

    logger.info("Scheduler configured (timezone: Asia/Tashkent)")
    return scheduler
