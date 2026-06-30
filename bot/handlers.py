import asyncio
import logging
import os
import re
from datetime import datetime
from html import escape

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.keyboards import (
    analytics_keyboard,
    budget_presets_keyboard,
    currency_keyboard,
    debts_keyboard,
    goal_delete_confirm_keyboard,
    goal_detail_keyboard,
    goals_list_keyboard,
    history_delete_keyboard,
    main_menu_keyboard,
    more_menu_keyboard,
    prev_report_keyboard,
    report_keyboard,
    reset_confirm_keyboard,
    saved_card_keyboard,
    settings_keyboard,
    webapp_keyboard,
)
from services.currency_service import (
    CURRENCIES,
    DEFAULT_CURRENCY,
    convert,
    normalize_currency,
)
from services.groq_service import (
    get_savings_tips,
    parse_amount_text,
    parse_debt_text,
    parse_goal_text,
    parse_photo_receipt,
    parse_text_purchase,
    transcribe_audio,
)
from services.supabase_service import (
    add_goal_contribution,
    count_transactions,
    create_debt,
    create_goal,
    delete_all_transactions,
    delete_goal,
    delete_last_transaction,
    delete_transaction,
    get_budget_status,
    get_daily_spent_last_n,
    get_debts,
    get_goal,
    get_goals,
    get_last_transactions,
    get_month_spent_through_day,
    get_monthly_summary,
    get_month_report_data,
    get_monthly_summary_for,
    get_or_create_user,
    make_budget_status,
    mark_notif_sent,
    notify_settings_of,
    now_local,
    save_transaction,
    update_budget,
    update_currency,
    update_goal,
    update_language,
    update_notify_settings,
)
from utils.formatters import (
    CATEGORY_EMOJI,
    compute_analytics,
    count_label,
    format_amount,
    format_analytics_card,
    format_budget_alert,
    format_debt_created,
    format_debts_list,
    format_goal_card,
    format_goals_list,
    format_history,
    format_large_tx_alert,
    format_month_overview,
    format_monthly_report,
    format_saved_card,
    format_tips,
    goal_progress,
)
from utils.i18n import t

logger = logging.getLogger(__name__)


# ───────────────────────── Helpers ─────────────────────────

async def _get_user(update: Update) -> dict:
    u = update.effective_user
    return await get_or_create_user(u.id, u.username, u.first_name)


async def _typing(update: Update, action: ChatAction = ChatAction.TYPING) -> None:
    try:
        await update.effective_chat.send_action(action)
    except Exception:
        pass


def _budget_of(user: dict) -> float:
    return float(user.get("monthly_budget", 5_000_000) or 5_000_000)


def _currency_of(user: dict) -> str:
    return normalize_currency(user.get("currency"), DEFAULT_CURRENCY)


def _webapp_url() -> str:
    url = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    if not url:
        dom = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if dom:
            url = "https://" + dom.rstrip("/")
    if url:
        # Cache-bust the Mini App webview on each deploy (see main._version_tag).
        tag = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("RAILWAY_DEPLOYMENT_ID") or "").strip()[:8]
        if tag:
            url += ("&" if "?" in url else "?") + "v=" + tag
    return url


def _parse_money(s: str) -> float:
    """'10 000 000' / '12000000' / '1,5 млн' / '500k' / '200к' → float."""
    s = re.sub(r"\s", "", (s or "").strip().lower())  # drop spaces incl. NBSP
    mult = 1.0
    for suf, m in (("млрд", 1e9), ("млн", 1e6), ("тыс", 1e3), ("kk", 1e6),
                   ("k", 1e3), ("к", 1e3), ("m", 1e6), ("м", 1e6)):
        if s.endswith(suf):
            mult = m
            s = s[: -len(suf)]
            break
    s = s.replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    if not s or s == ".":
        return 0.0
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


_GOAL_EMOJI_HINTS = [
    (("отпуск", "путеш", "vacation", "travel", "trip", "отдых"), "🏖"),
    (("машин", "авто", "car", "тачк"), "🚗"),
    (("дом", "кварт", "house", "home", "apartment", "ремонт"), "🏠"),
    (("ноут", "комп", "laptop", "pc", "macbook"), "💻"),
    (("телеф", "phone", "iphone", "айфон", "смартф"), "📱"),
    (("свадь", "wedding", "кольц", "ring"), "💍"),
    (("учеб", "образ", "курс", "study", "course", "education", "школ", "universit"), "🎓"),
    (("подуш", "fund", "запас", "резерв", "emergency", "rainy"), "🛟"),
    (("камер", "фото", "camera"), "📷"),
    (("велос", "bike", "bicycle"), "🚲"),
    (("игр", "console", "playstation", "xbox", "ps5"), "🎮"),
    (("подар", "gift", "present"), "🎁"),
]


def _guess_goal_emoji(title: str) -> str:
    # If the title already starts with an emoji, keep it.
    t0 = (title or "").strip()
    if t0 and ord(t0[0]) > 0x2190:
        return t0[0]
    low = t0.lower()
    for keys, emo in _GOAL_EMOJI_HINTS:
        if any(k in low for k in keys):
            return emo
    return "🎯"


def _parse_goal_structured(text: str):
    """Parse a typed goal in the 'Название · сумма · срок' format.
    Returns (title, amount, deadline_iso|None); amount=0 if it doesn't fit."""
    parts = [p.strip() for p in re.split(r"[·;|\n]+", text) if p.strip()]
    if len(parts) < 2:
        return "", 0.0, None
    title = parts[0][:120]
    amount = _parse_money(parts[1])
    deadline = None
    if len(parts) >= 3:
        try:
            deadline = datetime.strptime(parts[2][:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            deadline = None
    return title, amount, deadline


async def _build_context(user: dict, user_id: int, summary: list | None = None):
    """Returns (user_context, budget, prior_spent, currency, prior_avg).
    Pass `summary` if already fetched (e.g. concurrently with the user row) to
    skip the monthly-summary query."""
    budget = _budget_of(user)
    currency = _currency_of(user)
    if summary is None:
        summary = await get_monthly_summary(user_id)
    spent = sum(float(r.get("total_spent", 0)) for r in summary)
    count = sum(int(r.get("num_transactions", 0)) for r in summary)
    avg = spent / count if count else 0.0
    user_context = {
        "language": user.get("language", "ru"),
        "monthly_budget": budget,
        "spent_this_month": spent,
        "currency": currency,
    }
    return user_context, budget, spent, currency, avg


async def _save_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
    lang: str,
    user_id: int,
    input_type: str,
    budget: float,
    prior_spent: float,
    currency: str,
    user: dict,
    prior_avg: float = 0.0,
) -> None:
    """Persist a parsed purchase (converting currency if needed) and reply with a
    beautiful card + inline actions, then fire any smart notifications."""
    if not result.get("amount"):
        await update.message.reply_text(t("parse_error", lang), parse_mode="HTML")
        return

    # ── Multi-currency: convert the entry amount into the user's base currency ──
    entry_currency = normalize_currency(result.get("currency"), currency)
    entry_amount = float(result["amount"])
    fx_note = None
    original_amount = None
    original_currency = None
    base_amount = entry_amount
    if entry_currency != currency:
        base_amount = await convert(entry_amount, entry_currency, currency)
        original_amount = entry_amount
        original_currency = entry_currency
        fx_note = f"{format_amount(entry_amount, entry_currency)} → {format_amount(base_amount, currency)}"

    result["amount"] = base_amount  # card + budget math use the base amount

    saved = await save_transaction(
        user_id,
        base_amount,
        result["category"],
        result.get("description", ""),
        result.get("merchant"),
        result.get("advice", ""),
        input_type,
        original_amount=original_amount,
        original_currency=original_currency,
    )
    tx_id = saved.get("id") if saved else None

    spent_after = prior_spent + base_amount
    status = make_budget_status(budget, spent_after)
    card = format_saved_card(result, status, lang, input_type, currency=currency, fx_note=fx_note)
    await update.message.reply_text(
        card, parse_mode="HTML", reply_markup=saved_card_keyboard(tx_id, lang)
    )

    # Smart notifications (budget thresholds + large purchase).
    await _send_alerts(
        context, update.effective_chat.id, user_id, lang, currency,
        budget, prior_spent, spent_after, base_amount,
        result.get("category", ""), prior_avg, user,
    )


async def _send_alerts(
    context, chat_id, user_id, lang, currency,
    budget, prior_spent, spent_after, base_amount, category, prior_avg, user,
):
    """Fire real-time alerts after a save, honouring the user's notify toggles
    (reusing the user row already loaded by the handler — no extra DB round-trip)."""
    settings = notify_settings_of(user)

    month_key = now_local().strftime("%Y-%m")

    # Budget threshold crossings (deduped once per month per threshold).
    if settings.get("budget_alerts") and budget > 0:
        prior_pct = prior_spent / budget * 100
        new_pct = spent_after / budget * 100
        crossing = None
        if new_pct >= 100 > prior_pct:
            crossing = f"budget_100:{month_key}"
        elif new_pct >= 80 > prior_pct:
            crossing = f"budget_80:{month_key}"
        if crossing and await mark_notif_sent(user_id, "budget", crossing):
            status = make_budget_status(budget, spent_after)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=format_budget_alert(status, lang, currency=currency),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("budget alert failed user=%s: %s", user_id, e)

    # Large single purchase: ≥3× the running average, with enough prior history.
    if settings.get("large_tx") and prior_avg > 0 and base_amount >= 3 * prior_avg:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_large_tx_alert(base_amount, category, prior_avg, lang, currency=currency),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("large-tx alert failed user=%s: %s", user_id, e)


async def _analytics_payload(user_id: int, budget: float, lang: str, currency: str, offset: int = 0):
    """Build (text, keyboard) for the analytics card. offset=0 current month, -1 prev, etc."""
    now = now_local()
    year, month = now.year, now.month
    for _ in range(-offset):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    is_current = offset == 0
    month_first = f"{year}-{month:02d}-01"

    sparkline = None
    prev_through = None
    if is_current:
        ref = now
        pm = month - 1 or 12
        py = year if month > 1 else year - 1
        # Three independent reads — run them concurrently.
        summary, sparkline, prev_through = await asyncio.gather(
            get_monthly_summary_for(user_id, month_first),
            get_daily_spent_last_n(user_id, 7),
            get_month_spent_through_day(user_id, py, pm, now.day),
        )
    else:
        ref = datetime(year, month, 1)
        summary = await get_monthly_summary_for(user_id, month_first)

    a = compute_analytics(
        summary, budget, ref,
        sparkline=sparkline, prev_through_day=prev_through, is_current=is_current,
    )
    text = format_analytics_card(a, lang, currency=currency)
    return text, analytics_keyboard(lang, offset)


async def _goals_payload(user_id: int, lang: str):
    goals = await get_goals(user_id)
    today = now_local().date()
    text = format_goals_list(goals, lang, today=today)
    if goals:
        text += f"\n\n<i>{t('goals_hint', lang)}</i>"
    return text, goals_list_keyboard(goals, lang)


def _currency_payload(user: dict, lang: str):
    cur = _currency_of(user)
    text = t("currency_prompt", lang, currency=f"{CURRENCIES[cur]['flag']} {cur}")
    return text, currency_keyboard(cur, lang)


def _settings_payload(user: dict, lang: str):
    settings = notify_settings_of(user)
    return t("settings_title", lang), settings_keyboard(settings, lang)


# ───────────────────────── Commands ─────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        context.user_data.pop("await", None)
        user = await _get_user(update)
        lang = user.get("language", "ru")
        name = update.effective_user.first_name or ("друг" if lang == "ru" else "friend")
        logger.info("start telegram_id=%s", update.effective_user.id)
        await update.message.reply_text(
            t("welcome", lang, name=name),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(lang),
        )
        url = _webapp_url()
        if url:
            await update.message.reply_text(
                t("open_app_hint", lang), parse_mode="HTML", reply_markup=webapp_keyboard(url, lang)
            )
    except Exception as e:
        logger.error("start_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def app_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        url = _webapp_url()
        if not url:
            await update.message.reply_text(t("app_unavailable", lang), parse_mode="HTML")
            return
        await update.message.reply_text(
            t("open_app_hint", lang), parse_mode="HTML", reply_markup=webapp_keyboard(url, lang)
        )
    except Exception as e:
        logger.error("app_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        await update.message.reply_text(
            t("help_text", lang), parse_mode="HTML", reply_markup=main_menu_keyboard(lang)
        )
    except Exception as e:
        logger.error("help_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        currency = _currency_of(user)
        logger.info("budget telegram_id=%s args=%s", user_id, context.args)

        if not context.args:
            await budget_view_handler(update, context, user=user)
            return

        raw = "".join(context.args).replace(" ", "").replace(",", "")
        try:
            amount = float(raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(t("budget_invalid", lang), parse_mode="HTML")
            return

        await update_budget(user_id, amount)
        await update.message.reply_text(
            t("budget_set", lang, amount=format_amount(amount, currency)), parse_mode="HTML"
        )
    except Exception as e:
        logger.error("budget_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def budget_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict = None) -> None:
    """Show current budget gauge + preset quick-set buttons."""
    try:
        user = user or await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        budget = _budget_of(user)
        currency = _currency_of(user)
        status = await get_budget_status(user_id, budget=budget)
        from utils.formatters import zone_dot
        pct = status["percent"]
        await update.message.reply_text(
            t("budget_view", lang, amount=format_amount(budget, currency), gauge=f"{zone_dot(pct)} {pct:.1f}%"),
            parse_mode="HTML",
            reply_markup=budget_presets_keyboard(lang),
        )
    except Exception as e:
        logger.error("budget_view_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        currency = _currency_of(user)
        logger.info("report telegram_id=%s", user_id)

        data = await get_month_report_data(user_id, currency)
        if not data.get("has_activity"):
            await update.message.reply_text(t("empty_report", lang), parse_mode="HTML")
            return

        await update.message.reply_text(
            format_month_overview(data, lang, currency=currency),
            parse_mode="HTML",
            reply_markup=report_keyboard(lang),
        )
    except Exception as e:
        logger.error("report_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def analytics_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("analytics telegram_id=%s", user_id)

        await _typing(update)
        text, kb = await _analytics_payload(user_id, _budget_of(user), lang, _currency_of(user), offset=0)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("analytics_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        currency = _currency_of(user)
        logger.info("history telegram_id=%s", user_id)

        transactions = await get_last_transactions(user_id, 10)
        if not transactions:
            await update.message.reply_text(t("empty_history", lang), parse_mode="HTML")
            return

        text = f"{format_history(transactions, lang, currency=currency)}\n\n<i>{t('history_hint', lang)}</i>"
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=history_delete_keyboard(transactions, lang)
        )
    except Exception as e:
        logger.error("history_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def tips_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        currency = _currency_of(user)
        logger.info("tips telegram_id=%s", user_id)

        summary = await get_monthly_summary(user_id)
        if not summary:
            await update.message.reply_text(t("no_data_tips", lang), parse_mode="HTML")
            return

        await update.message.reply_text(t("tips_loading", lang), parse_mode="HTML")
        await _typing(update)
        tips = await get_savings_tips(summary, lang, currency)
        if tips:
            await update.message.reply_text(format_tips(tips, lang), parse_mode="HTML")
        else:
            await update.message.reply_text(t("no_data_tips", lang), parse_mode="HTML")
    except Exception as e:
        logger.error("tips_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def lang_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("lang telegram_id=%s args=%s", user_id, context.args)

        if not context.args or context.args[0] not in ("ru", "en"):
            await update.message.reply_text(t("lang_usage", lang), parse_mode="HTML")
            return

        new_lang = context.args[0]
        await update_language(user_id, new_lang)
        # Re-send the menu so the reply-keyboard labels switch language.
        await update.message.reply_text(
            t("lang_changed", new_lang), parse_mode="HTML", reply_markup=main_menu_keyboard(new_lang)
        )
    except Exception as e:
        logger.error("lang_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def goals_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        context.user_data.pop("await", None)
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("goals telegram_id=%s", user_id)
        text, kb = await _goals_payload(user_id, lang)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("goals_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def debts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("debts telegram_id=%s", user_id)
        debts = await get_debts(user_id)
        text = format_debts_list(debts, lang, today=now_local().date())
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=debts_keyboard(_webapp_url(), lang)
        )
    except Exception as e:
        logger.error("debts_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def currency_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("currency telegram_id=%s args=%s", user_id, context.args)

        if context.args:
            code = normalize_currency(context.args[0], "")
            if code not in CURRENCIES:
                await update.message.reply_text(
                    t("currency_usage", lang, codes=", ".join(CURRENCIES.keys())), parse_mode="HTML"
                )
                return
            await update_currency(user_id, code)
            await update.message.reply_text(
                t("currency_changed", lang, currency=f"{CURRENCIES[code]['flag']} {code}"), parse_mode="HTML"
            )
            return

        text, kb = _currency_payload(user, lang)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("currency_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        logger.info("settings telegram_id=%s", update.effective_user.id)
        text, kb = _settings_payload(user, lang)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("settings_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def more_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        await update.message.reply_text(
            t("more_menu", lang), parse_mode="HTML", reply_markup=more_menu_keyboard(lang)
        )
    except Exception as e:
        logger.error("more_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def undo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        currency = _currency_of(user)
        logger.info("undo telegram_id=%s", user_id)

        deleted = await delete_last_transaction(user_id)
        if not deleted:
            await update.message.reply_text(t("undo_empty", lang), parse_mode="HTML")
            return

        emoji = CATEGORY_EMOJI.get(deleted.get("category", "Другое"), "📦")
        await update.message.reply_text(
            t("undo_done", lang, emoji=emoji, category=deleted.get("category", ""),
              amount=format_amount(deleted.get("amount", 0), currency)),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("undo_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("reset telegram_id=%s", user_id)

        n = await count_transactions(user_id)
        if n == 0:
            await update.message.reply_text(t("reset_empty", lang), parse_mode="HTML")
            return

        await update.message.reply_text(
            t("reset_confirm", lang, count=count_label(n, lang)),
            parse_mode="HTML",
            reply_markup=reset_confirm_keyboard(lang),
        )
    except Exception as e:
        logger.error("reset_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


# ───────────────────────── Goal flows ─────────────────────────

async def _show_goal_card(query, user_id: int, goal: dict, lang: str):
    today = now_local().date()
    text = f"{format_goal_card(goal, lang, today=today)}\n\n<i>{t('goal_detail_hint', lang)}</i>"
    await _safe_edit(query, text, parse_mode="HTML", reply_markup=goal_detail_keyboard(goal, lang))


def _goal_contributed_text(goal: dict, amount: float, lang: str) -> str:
    p = goal_progress(goal)
    cur = goal.get("currency") or DEFAULT_CURRENCY
    return t(
        "goal_contributed", lang,
        amount=format_amount(amount, cur),
        title=escape(str(goal.get("title", ""))),
        pct=f"{p['percent']:.0f}",
    )


async def _handle_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE, pend: dict, text: str) -> bool:
    """Process a message expected by a pending goal flow. Returns True if consumed."""
    user = await _get_user(update)
    lang = user.get("language", "ru")
    user_id = update.effective_user.id
    base_currency = _currency_of(user)
    ptype = pend.get("type")

    if ptype == "goal_new":
        # Fast path: typed "Название · сумма · срок". Falls back to the LLM for
        # natural language / transcribed speech ("Отпуск, десять миллионов, к декабрю").
        title, amount, deadline = _parse_goal_structured(text)
        if not (title and amount > 0):
            gd = await parse_goal_text(
                text, {"language": lang, "currency": base_currency, "today": now_local().date().isoformat()}
            )
            title = (gd.get("title") or title or "").strip()[:120]
            amount = gd.get("amount") or amount
            deadline = gd.get("deadline") or deadline
        if not title or not amount or amount <= 0:
            await update.message.reply_text(t("goal_create_invalid", lang), parse_mode="HTML")
            return True  # keep pending so the user can retry
        emoji = _guess_goal_emoji(title)
        goal = await create_goal(user_id, title, amount, base_currency, emoji=emoji, deadline=deadline)
        context.user_data.pop("await", None)
        if not goal:
            await update.message.reply_text(t("error_generic", lang), parse_mode="HTML")
            return True
        today = now_local().date()
        body = (
            f"{t('goal_created', lang)}\n\n"
            f"{format_goal_card(goal, lang, today=today)}\n\n<i>{t('goal_detail_hint', lang)}</i>"
        )
        await update.message.reply_text(body, parse_mode="HTML", reply_markup=goal_detail_keyboard(goal, lang))
        return True

    if ptype == "debt_new":
        gd = await parse_debt_text(
            text, {"language": lang, "currency": base_currency, "today": now_local().date().isoformat()}
        )
        counterparty = (gd.get("counterparty") or "").strip()
        amount = gd.get("amount") or 0
        if not counterparty or amount <= 0:
            await update.message.reply_text(t("debt_create_invalid", lang), parse_mode="HTML")
            return True  # keep pending so the user can retry
        debt = await create_debt(
            user_id, gd.get("direction", "owed_to_me"), counterparty, amount,
            gd.get("currency") or base_currency, due_date=gd.get("deadline"),
        )
        context.user_data.pop("await", None)
        if not debt:
            await update.message.reply_text(t("error_generic", lang), parse_mode="HTML")
            return True
        await update.message.reply_text(
            format_debt_created(debt, lang, today=now_local().date()),
            parse_mode="HTML", reply_markup=debts_keyboard(_webapp_url(), lang),
        )
        return True

    if ptype == "goal_add":
        amount = _parse_money(text)
        if amount <= 0:  # voice / worded amount → ask the LLM to read the number
            amount = await parse_amount_text(text, base_currency)
        if amount <= 0:
            await update.message.reply_text(t("goal_contribute_invalid", lang), parse_mode="HTML")
            return True
        goal_id = pend.get("goal_id")
        before = await get_goal(user_id, goal_id)
        if not before:
            context.user_data.pop("await", None)
            await update.message.reply_text(t("goal_not_found", lang), parse_mode="HTML")
            return True
        was_done = (before.get("status") == "done")
        goal = await add_goal_contribution(user_id, goal_id, amount)
        context.user_data.pop("await", None)
        if not goal:
            await update.message.reply_text(t("error_generic", lang), parse_mode="HTML")
            return True
        await update.message.reply_text(_goal_contributed_text(goal, amount, lang), parse_mode="HTML")
        if goal.get("status") == "done" and not was_done:
            cur = goal.get("currency") or DEFAULT_CURRENCY
            await update.message.reply_text(
                t("goal_done_celebrate", lang, title=escape(str(goal.get("title", ""))),
                  amount=format_amount(goal.get("target_amount", 0), cur)),
                parse_mode="HTML",
            )
        return True

    if ptype == "goal_editdl":
        goal_id = pend.get("goal_id")
        raw = text.strip()
        if raw in ("-", "—", "–"):
            await update_goal(user_id, goal_id, {"deadline": None})
            context.user_data.pop("await", None)
            await update.message.reply_text(t("goal_deadline_cleared", lang), parse_mode="HTML")
            return True
        try:
            dl = datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            await update.message.reply_text(t("goal_edit_deadline_prompt", lang), parse_mode="HTML")
            return True
        await update_goal(user_id, goal_id, {"deadline": dl})
        context.user_data.pop("await", None)
        await update.message.reply_text(t("goal_deadline_set", lang), parse_mode="HTML")
        goal = await get_goal(user_id, goal_id)
        if goal:
            today = now_local().date()
            await update.message.reply_text(
                f"{format_goal_card(goal, lang, today=today)}\n\n<i>{t('goal_detail_hint', lang)}</i>",
                parse_mode="HTML", reply_markup=goal_detail_keyboard(goal, lang),
            )
        return True

    # Unknown pending type — drop it.
    context.user_data.pop("await", None)
    return False


# ───────────────────────── Callbacks ─────────────────────────

async def _safe_edit(query, text, **kwargs):
    """edit_message_text that swallows the 'message is not modified' noise."""
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        budget = _budget_of(user)
        currency = _currency_of(user)
        data = query.data or ""
        logger.info("callback telegram_id=%s data=%s", user_id, data)

        # ── Savings goals ──
        if data.startswith("goal:"):
            await _goal_callback(update, context, query, user_id, lang, data)
            return

        # ── Add a debt from chat (text/voice) ──
        if data == "debt:new":
            context.user_data["await"] = {"type": "debt_new"}
            await query.answer()
            await query.message.reply_text(t("debt_new_prompt", lang), parse_mode="HTML")
            return

        # ── Currency picker ──
        if data.startswith("cur:"):
            code = normalize_currency(data.split(":", 1)[1], "")
            if code not in CURRENCIES:
                await query.answer()
                return
            await update_currency(user_id, code)
            await query.answer("✅")
            text = t("currency_changed", lang, currency=f"{CURRENCIES[code]['flag']} {code}")
            await _safe_edit(query, text, parse_mode="HTML", reply_markup=currency_keyboard(code, lang))
            return

        # ── Notification toggles ──
        if data.startswith("nset:"):
            key = data.split(":", 1)[1]
            settings = notify_settings_of(user)
            if key in settings:
                settings[key] = not settings[key]
                settings = await update_notify_settings(user_id, settings)
            await query.answer()
            await _safe_edit(query, t("settings_title", lang), parse_mode="HTML",
                             reply_markup=settings_keyboard(settings, lang))
            return

        # ── Delete one transaction (history) ──
        if data.startswith("del:"):
            try:
                tx_id = int(data.split(":", 1)[1])
            except ValueError:
                await query.answer()
                return
            deleted = await delete_transaction(user_id, tx_id)
            await query.answer(t("delete_done", lang) if deleted else t("delete_failed", lang))
            transactions = await get_last_transactions(user_id, 10)
            if not transactions:
                await _safe_edit(query, t("empty_history", lang), parse_mode="HTML")
                return
            text = f"{format_history(transactions, lang, currency=currency)}\n\n<i>{t('history_hint', lang)}</i>"
            await _safe_edit(query, text, parse_mode="HTML",
                             reply_markup=history_delete_keyboard(transactions, lang))
            return

        # ── Undo a specific just-saved transaction ──
        if data.startswith("undo:"):
            key = data.split(":", 1)[1]
            if key == "last":
                deleted = await delete_last_transaction(user_id)
            else:
                try:
                    deleted = await delete_transaction(user_id, int(key))
                except ValueError:
                    deleted = {}
            await query.answer(t("delete_done", lang) if deleted else t("delete_failed", lang))
            if deleted:
                emoji = CATEGORY_EMOJI.get(deleted.get("category", "Другое"), "📦")
                await _safe_edit(
                    query,
                    t("undo_done", lang, emoji=emoji, category=deleted.get("category", ""),
                      amount=format_amount(deleted.get("amount", 0), currency)),
                    parse_mode="HTML",
                )
            return

        # ── History refresh ──
        if data == "hist:refresh":
            transactions = await get_last_transactions(user_id, 10)
            await query.answer("🔄")
            if not transactions:
                await _safe_edit(query, t("empty_history", lang), parse_mode="HTML")
                return
            text = f"{format_history(transactions, lang, currency=currency)}\n\n<i>{t('history_hint', lang)}</i>"
            await _safe_edit(query, text, parse_mode="HTML",
                             reply_markup=history_delete_keyboard(transactions, lang))
            return

        # ── Analytics: refresh / month navigation (edit in place) ──
        if data.startswith("ana:"):
            try:
                offset = int(data.split(":", 1)[1])
            except ValueError:
                offset = 0
            await query.answer()
            text, kb = await _analytics_payload(user_id, budget, lang, currency, offset=offset)
            await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
            return

        # ── Analytics: open as a NEW message (from saved card / report / history) ──
        if data == "nav:analytics":
            await query.answer()
            text, kb = await _analytics_payload(user_id, budget, lang, currency, offset=0)
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return

        # ── Open report (new message) ──
        if data == "nav:report":
            await query.answer()
            rep = await get_month_report_data(user_id, currency)
            if not rep.get("has_activity"):
                await query.message.reply_text(t("empty_report", lang), parse_mode="HTML")
                return
            await query.message.reply_text(
                format_month_overview(rep, lang, currency=currency),
                parse_mode="HTML", reply_markup=report_keyboard(lang),
            )
            return

        # ── Previous-month report (new message) ──
        if data == "rep:prev":
            await query.answer()
            now = now_local()
            pm = now.month - 1 or 12
            py = now.year if now.month > 1 else now.year - 1
            month_first = f"{py}-{pm:02d}-01"
            summary = await get_monthly_summary_for(user_id, month_first)
            from utils.formatters import _month_name_for
            label = _month_name_for(py, pm, lang)
            if not summary:
                await query.message.reply_text(t("empty_report", lang), parse_mode="HTML")
                return
            spent = sum(float(r.get("total_spent", 0)) for r in summary)
            status = make_budget_status(budget, spent)
            await query.message.reply_text(
                format_monthly_report(summary, status, lang, month_label=label, currency=currency),
                parse_mode="HTML",
                reply_markup=prev_report_keyboard(lang),
            )
            return

        # ── Tips (new message, LLM) ──
        if data == "nav:tips":
            await query.answer()
            summary = await get_monthly_summary(user_id)
            if not summary:
                await query.message.reply_text(t("no_data_tips", lang), parse_mode="HTML")
                return
            await query.message.reply_text(t("tips_loading", lang), parse_mode="HTML")
            await _typing(update)
            tips = await get_savings_tips(summary, lang, currency)
            await query.message.reply_text(
                format_tips(tips, lang) if tips else t("no_data_tips", lang), parse_mode="HTML"
            )
            return

        # ── Budget presets ──
        if data.startswith("bud:"):
            val = data.split(":", 1)[1]
            if val == "custom":
                await query.answer()
                await query.message.reply_text(t("budget_custom_prompt", lang), parse_mode="HTML")
                return
            try:
                amount = float(val)
            except ValueError:
                await query.answer()
                return
            await update_budget(user_id, amount)
            await query.answer("✅")
            await _safe_edit(
                query, t("budget_set", lang, amount=format_amount(amount, currency)), parse_mode="HTML"
            )
            return

        # ── More menu actions ──
        if data == "more:currency":
            await query.answer()
            text, kb = _currency_payload(user, lang)
            await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
            return

        if data == "more:settings":
            await query.answer()
            text, kb = _settings_payload(user, lang)
            await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
            return

        if data == "lang:toggle":
            new_lang = "en" if lang == "ru" else "ru"
            await update_language(user_id, new_lang)
            await query.answer()
            await _safe_edit(query, t("lang_changed", new_lang), parse_mode="HTML")
            await query.message.reply_text(
                t("menu_hint", new_lang), reply_markup=main_menu_keyboard(new_lang)
            )
            return

        if data == "more:help":
            await query.answer()
            await query.message.reply_text(
                t("help_text", lang), parse_mode="HTML", reply_markup=main_menu_keyboard(lang)
            )
            return

        if data == "reset:ask":
            await query.answer()
            n = await count_transactions(user_id)
            if n == 0:
                await query.message.reply_text(t("reset_empty", lang), parse_mode="HTML")
                return
            await query.message.reply_text(
                t("reset_confirm", lang, count=count_label(n, lang)),
                parse_mode="HTML", reply_markup=reset_confirm_keyboard(lang),
            )
            return

        # ── Reset confirmation ──
        if data == "reset:yes":
            count = await delete_all_transactions(user_id)
            await query.answer()
            await _safe_edit(query, t("reset_done", lang, count=count_label(count, lang)), parse_mode="HTML")
            return

        if data == "reset:no":
            await query.answer()
            await _safe_edit(query, t("reset_cancelled", lang), parse_mode="HTML")
            return

        await query.answer()
    except Exception as e:
        logger.error("callback_handler error: %s", e)
        try:
            await query.answer(t("error_generic", "ru"))
        except Exception:
            pass


async def _goal_callback(update, context, query, user_id, lang, data):
    """Handle all goal:* callbacks."""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    gid = None
    if len(parts) > 2:
        try:
            gid = int(parts[2])
        except ValueError:
            gid = None

    if action == "list":
        await query.answer()
        text, kb = await _goals_payload(user_id, lang)
        await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
        return

    if action == "new":
        context.user_data["await"] = {"type": "goal_new"}
        await query.answer()
        await query.message.reply_text(t("goal_new_prompt", lang), parse_mode="HTML")
        return

    if action == "open" and gid is not None:
        goal = await get_goal(user_id, gid)
        if not goal:
            await query.answer(t("goal_not_found", lang))
            text, kb = await _goals_payload(user_id, lang)
            await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
            return
        await query.answer()
        await _show_goal_card(query, user_id, goal, lang)
        return

    if action == "add" and gid is not None:
        goal = await get_goal(user_id, gid)
        if not goal:
            await query.answer(t("goal_not_found", lang))
            return
        context.user_data["await"] = {"type": "goal_add", "goal_id": gid}
        cur = goal.get("currency") or DEFAULT_CURRENCY
        await query.answer()
        await query.message.reply_text(
            t("goal_contribute_prompt", lang, title=escape(str(goal.get("title", ""))), currency=cur),
            parse_mode="HTML",
        )
        return

    if action == "fill" and gid is not None:
        goal = await get_goal(user_id, gid)
        if not goal:
            await query.answer(t("goal_not_found", lang))
            return
        p = goal_progress(goal)
        if p["remaining"] <= 0:
            await query.answer()
            await _show_goal_card(query, user_id, goal, lang)
            return
        was_done = goal.get("status") == "done"
        updated = await add_goal_contribution(user_id, gid, p["remaining"])
        await query.answer("🎉")
        if updated:
            await _show_goal_card(query, user_id, updated, lang)
            if updated.get("status") == "done" and not was_done:
                cur = updated.get("currency") or DEFAULT_CURRENCY
                await query.message.reply_text(
                    t("goal_done_celebrate", lang, title=escape(str(updated.get("title", ""))),
                      amount=format_amount(updated.get("target_amount", 0), cur)),
                    parse_mode="HTML",
                )
        return

    if action == "editdl" and gid is not None:
        context.user_data["await"] = {"type": "goal_editdl", "goal_id": gid}
        await query.answer()
        await query.message.reply_text(t("goal_edit_deadline_prompt", lang), parse_mode="HTML")
        return

    if action == "del" and gid is not None:
        goal = await get_goal(user_id, gid)
        if not goal:
            await query.answer(t("goal_not_found", lang))
            return
        await query.answer()
        title = escape(str(goal.get("title", "")))
        prompt = (
            f"🗑 <b>Удалить цель «{title}»?</b>" if lang == "ru" else f"🗑 <b>Delete goal “{title}”?</b>"
        )
        await _safe_edit(query, prompt, parse_mode="HTML", reply_markup=goal_delete_confirm_keyboard(gid, lang))
        return

    if action == "delyes" and gid is not None:
        await delete_goal(user_id, gid)
        await query.answer(t("goal_deleted", lang))
        text, kb = await _goals_payload(user_id, lang)
        await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
        return

    await query.answer()


# ───────────────────────── Menu button routing ─────────────────────────
# Reply-keyboard taps arrive as plain text — route them BEFORE the purchase parser.

BUTTON_ROUTES = {
    "📊 Аналитика": "analytics", "📊 Analytics": "analytics",
    "📋 История": "history", "📋 History": "history",
    "🎯 Цели": "goals", "🎯 Goals": "goals",
    "💰 Бюджет": "budget_view", "💰 Budget": "budget_view",
    "💡 Советы": "tips", "💡 Tips": "tips",
    "↩️ Отменить": "undo", "↩️ Undo": "undo",
    "⚙️ Ещё": "more", "⚙️ More": "more",
}

_ROUTE_FUNCS = {}  # filled after handler defs below


# ───────────────────────── Messages ─────────────────────────

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        label = (update.message.text or "").strip()

        # Pending goal flow takes precedence — unless the user tapped a menu button.
        pend = context.user_data.get("await")
        if pend and label not in BUTTON_ROUTES:
            consumed = await _handle_pending_text(update, context, pend, label)
            if consumed:
                return
        elif pend and label in BUTTON_ROUTES:
            context.user_data.pop("await", None)

        route = BUTTON_ROUTES.get(label)
        if route:
            await _ROUTE_FUNCS[route](update, context)
            return

        user_id = update.effective_user.id
        logger.info("text message telegram_id=%s", user_id)

        await _typing(update)
        # User row and month summary are independent — fetch them concurrently.
        user, summary = await asyncio.gather(_get_user(update), get_monthly_summary(user_id))
        lang = user.get("language", "ru")
        user_context, budget, prior_spent, currency, prior_avg = await _build_context(user, user_id, summary)
        result = await parse_text_purchase(label, user_context)
        await _save_and_reply(update, context, result, lang, user_id, "text", budget, prior_spent, currency, user, prior_avg)
    except Exception as e:
        logger.error("text_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("voice message telegram_id=%s", user_id)

        await _typing(update)
        file = await context.bot.get_file(update.message.voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        transcribed = await transcribe_audio(audio_bytes, "voice.ogg")

        if not transcribed:
            await update.message.reply_text(t("parse_error", lang), parse_mode="HTML")
            return

        await update.message.reply_text(
            t("voice_transcribed", lang, text=escape(transcribed)), parse_mode="HTML"
        )
        # A pending goal flow (create / contribute) can be driven by voice too.
        pend = context.user_data.get("await")
        if pend:
            await _typing(update)
            if await _handle_pending_text(update, context, pend, transcribed):
                return
        await _typing(update)
        user_context, budget, prior_spent, currency, prior_avg = await _build_context(user, user_id)
        result = await parse_text_purchase(transcribed, user_context)
        await _save_and_reply(update, context, result, lang, user_id, "voice", budget, prior_spent, currency, user, prior_avg)
    except Exception as e:
        logger.error("voice_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("audio message telegram_id=%s", user_id)

        media = update.message.audio
        fname = media.file_name if media and media.file_name else "audio.mp3"

        await update.message.reply_text(t("audio_processing", lang), parse_mode="HTML")
        await _typing(update)

        file = await context.bot.get_file(media.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        transcribed = await transcribe_audio(audio_bytes, fname)

        if not transcribed:
            await update.message.reply_text(t("parse_error", lang), parse_mode="HTML")
            return

        await update.message.reply_text(
            t("voice_transcribed", lang, text=escape(transcribed)), parse_mode="HTML"
        )
        pend = context.user_data.get("await")
        if pend:
            await _typing(update)
            if await _handle_pending_text(update, context, pend, transcribed):
                return
        await _typing(update)
        user_context, budget, prior_spent, currency, prior_avg = await _build_context(user, user_id)
        result = await parse_text_purchase(transcribed, user_context)
        await _save_and_reply(update, context, result, lang, user_id, "audio", budget, prior_spent, currency, user, prior_avg)
    except Exception as e:
        logger.error("audio_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("photo message telegram_id=%s", user_id)

        await update.message.reply_text(t("photo_processing", lang), parse_mode="HTML")
        await _typing(update)

        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())

        user_context, budget, prior_spent, currency, prior_avg = await _build_context(user, user_id)
        result = await parse_photo_receipt(image_bytes, user_context)
        await _save_and_reply(update, context, result, lang, user_id, "photo", budget, prior_spent, currency, user, prior_avg)
    except Exception as e:
        logger.error("photo_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


# Wire menu-button routes to handler functions (after definitions).
_ROUTE_FUNCS.update({
    "analytics": analytics_handler,
    "history": history_handler,
    "goals": goals_handler,
    "budget_view": budget_view_handler,
    "tips": tips_handler,
    "undo": undo_handler,
    "more": more_handler,
})


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("app", app_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("budget", budget_handler))
    application.add_handler(CommandHandler("goals", goals_handler))
    application.add_handler(CommandHandler("debts", debts_handler))
    application.add_handler(CommandHandler("currency", currency_handler))
    application.add_handler(CommandHandler("settings", settings_handler))
    application.add_handler(CommandHandler("report", report_handler))
    application.add_handler(CommandHandler("analytics", analytics_handler))
    application.add_handler(CommandHandler("history", history_handler))
    application.add_handler(CommandHandler("tips", tips_handler))
    application.add_handler(CommandHandler("lang", lang_handler))
    application.add_handler(CommandHandler("undo", undo_handler))
    application.add_handler(CommandHandler("reset", reset_handler))
    application.add_handler(CommandHandler("more", more_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    application.add_handler(MessageHandler(filters.AUDIO, audio_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
