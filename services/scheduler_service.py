import logging
from datetime import timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.currency_service import DEFAULT_CURRENCY, normalize_currency
from services.supabase_service import (
    get_all_users,
    get_budget_status,
    get_debts,
    get_goals,
    get_monthly_summary,
    get_transactions_in_range,
    mark_notif_sent,
    notify_settings_of,
    now_local,
)
from utils.formatters import (
    format_daily_digest,
    format_debt_reminder,
    format_goal_pulse,
    format_monthly_report,
    format_weekly_summary,
)

logger = logging.getLogger(__name__)


def _sum_amount(rows: list) -> float:
    return sum(float(r.get("amount", 0) or 0) for r in rows)


def _category_totals(rows: list) -> dict:
    agg: dict[str, float] = {}
    for r in rows:
        cat = r.get("category", "Другое")
        agg[cat] = agg.get(cat, 0.0) + float(r.get("amount", 0) or 0)
    return agg


def setup_scheduler(application) -> AsyncIOScheduler:
    tz = pytz.timezone("Asia/Tashkent")
    scheduler = AsyncIOScheduler(timezone=tz)

    async def _send(tid, text):
        await application.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")

    # ── Monthly category report (last day of month, 18:00) ──
    async def send_monthly_reports():
        logger.info("scheduler: monthly reports")
        users = await get_all_users()
        for user in users:
            try:
                tid = user["telegram_id"]
                lang = user.get("language", "ru")
                cur = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
                summary = await get_monthly_summary(tid)
                if not summary:
                    continue
                status = await get_budget_status(tid, budget=float(user.get("monthly_budget") or 5_000_000))
                await _send(tid, format_monthly_report(summary, status, lang, currency=cur))
                logger.info("monthly report sent to %s", tid)
            except Exception as e:
                logger.warning("monthly report failed %s: %s", user.get("telegram_id"), e)

    # ── Daily wrap-up (21:00) — opt-in (off by default) ──
    async def send_daily_digests():
        logger.info("scheduler: daily digests")
        users = await get_all_users()
        today = now_local().date()
        dkey = f"daily_digest:{today.isoformat()}"
        for user in users:
            try:
                if not notify_settings_of(user).get("daily_digest"):
                    continue
                tid = user["telegram_id"]
                lang = user.get("language", "ru")
                cur = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
                # Claim first (opt-in digest always sends once a day).
                if not await mark_notif_sent(tid, "daily", dkey):
                    continue
                rows = await get_transactions_in_range(tid, today.isoformat(), today.isoformat())
                total = _sum_amount(rows)
                n = len(rows)
                top = None
                if rows:
                    agg = _category_totals(rows)
                    cat = max(agg, key=agg.get)
                    top = {"category": cat, "amount": agg[cat]}
                status = await get_budget_status(tid, budget=float(user.get("monthly_budget") or 5_000_000))
                await _send(tid, format_daily_digest(total, n, top, status, lang, currency=cur))
            except Exception as e:
                logger.warning("daily digest failed %s: %s", user.get("telegram_id"), e)

    # ── Weekly summary (Sunday 20:00) ──
    async def send_weekly_summaries():
        logger.info("scheduler: weekly summaries")
        users = await get_all_users()
        today = now_local().date()
        wkey = f"weekly:{today.isoformat()}"
        start = today - timedelta(days=6)
        pstart = today - timedelta(days=13)
        pend = today - timedelta(days=7)
        for user in users:
            try:
                if not notify_settings_of(user).get("weekly_summary"):
                    continue
                tid = user["telegram_id"]
                lang = user.get("language", "ru")
                cur = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
                rows = await get_transactions_in_range(tid, start.isoformat(), today.isoformat())
                prows = await get_transactions_in_range(tid, pstart.isoformat(), pend.isoformat())
                total = _sum_amount(rows)
                prev_total = _sum_amount(prows)
                # Skip truly inactive users (nothing this week or last).
                if total <= 0 and prev_total <= 0:
                    continue
                if not await mark_notif_sent(tid, "weekly", wkey):
                    continue
                agg = _category_totals(rows)
                top3 = [
                    {"category": c, "amount": a}
                    for c, a in sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:3]
                ]
                await _send(
                    tid,
                    format_weekly_summary(total, len(rows), top3, prev_total or None, lang, currency=cur),
                )
            except Exception as e:
                logger.warning("weekly summary failed %s: %s", user.get("telegram_id"), e)

    # ── Goal progress pulse — every morning (09:00) and evening (21:00) ──
    async def send_goal_pulse(part: str):
        logger.info("scheduler: goal pulse (%s)", part)
        users = await get_all_users()
        today = now_local().date()
        dkey = f"goal_pulse_{part}:{today.isoformat()}"
        for user in users:
            try:
                if not notify_settings_of(user).get("goal_reminders"):
                    continue
                tid = user["telegram_id"]
                lang = user.get("language", "ru")
                goals = await get_goals(tid)
                text = format_goal_pulse(goals, lang, part=part, today=today)
                if not text:  # no active goals → nothing to nudge about
                    continue
                if not await mark_notif_sent(tid, "goal_pulse", dkey):
                    continue
                await _send(tid, text)
            except Exception as e:
                logger.warning("goal pulse (%s) failed %s: %s", part, user.get("telegram_id"), e)

    scheduler.add_job(
        send_monthly_reports,
        trigger=CronTrigger(day="last", hour=18, minute=0),
        id="monthly_reports", replace_existing=True,
    )
    scheduler.add_job(
        send_daily_digests,
        trigger=CronTrigger(hour=21, minute=0),
        id="daily_digests", replace_existing=True,
    )
    scheduler.add_job(
        send_weekly_summaries,
        trigger=CronTrigger(day_of_week="sun", hour=20, minute=0),
        id="weekly_summaries", replace_existing=True,
    )
    # ── Debt/loan reminders — daily (10:00), debts due ≤3 days or overdue ──
    async def send_debt_reminders():
        logger.info("scheduler: debt reminders")
        users = await get_all_users()
        today = now_local().date()
        dkey = f"debt_reminder:{today.isoformat()}"
        for user in users:
            try:
                if not notify_settings_of(user).get("debt_reminders"):
                    continue
                tid = user["telegram_id"]
                lang = user.get("language", "ru")
                debts = await get_debts(tid, only_open=True)
                text = format_debt_reminder(debts, lang, today=today)
                if not text:  # nothing due soon → don't nag
                    continue
                if not await mark_notif_sent(tid, "debt", dkey):
                    continue
                await _send(tid, text)
            except Exception as e:
                logger.warning("debt reminder failed %s: %s", user.get("telegram_id"), e)

    scheduler.add_job(
        send_goal_pulse, args=["morning"],
        trigger=CronTrigger(hour=9, minute=0),
        id="goal_pulse_morning", replace_existing=True,
    )
    scheduler.add_job(
        send_debt_reminders,
        trigger=CronTrigger(hour=10, minute=0),
        id="debt_reminders", replace_existing=True,
    )
    scheduler.add_job(
        send_goal_pulse, args=["evening"],
        trigger=CronTrigger(hour=21, minute=0),
        id="goal_pulse_evening", replace_existing=True,
    )

    logger.info("Scheduler configured (timezone: Asia/Tashkent)")
    return scheduler
