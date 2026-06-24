import logging
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
    history_delete_keyboard,
    main_menu_keyboard,
    more_menu_keyboard,
    prev_report_keyboard,
    report_keyboard,
    reset_confirm_keyboard,
    saved_card_keyboard,
)
from services.groq_service import (
    get_savings_tips,
    parse_photo_receipt,
    parse_text_purchase,
    transcribe_audio,
)
from services.supabase_service import (
    delete_all_transactions,
    delete_last_transaction,
    delete_transaction,
    get_budget_status,
    get_daily_spent_last_n,
    get_last_transactions,
    get_month_spent,
    get_month_spent_through_day,
    get_monthly_summary,
    get_monthly_summary_for,
    get_or_create_user,
    make_budget_status,
    now_local,
    save_transaction,
    update_budget,
    update_language,
)
from utils.formatters import (
    CATEGORY_EMOJI,
    compute_analytics,
    count_label,
    format_amount,
    format_analytics_card,
    format_budget_status,
    format_history,
    format_monthly_report,
    format_saved_card,
    format_tips,
    build_saved_pace,
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


async def _build_context(user: dict, user_id: int) -> tuple[dict, float, float]:
    """One DB query (spent this month). Returns (user_context, budget, prior_spent)."""
    budget = _budget_of(user)
    spent = await get_month_spent(user_id)
    user_context = {
        "language": user.get("language", "ru"),
        "monthly_budget": budget,
        "spent_this_month": spent,
    }
    return user_context, budget, spent


async def _save_and_reply(
    update: Update,
    result: dict,
    lang: str,
    user_id: int,
    input_type: str,
    budget: float,
    prior_spent: float,
) -> None:
    """Persist a parsed purchase and reply with a beautiful card + inline actions."""
    if not result.get("amount"):
        await update.message.reply_text(t("parse_error", lang), parse_mode="HTML")
        return

    saved = await save_transaction(
        user_id,
        result["amount"],
        result["category"],
        result.get("description", ""),
        result.get("merchant"),
        result.get("advice", ""),
        input_type,
    )
    tx_id = saved.get("id") if saved else None

    spent_after = prior_spent + result["amount"]
    status = make_budget_status(budget, spent_after)
    pace = build_saved_pace(budget, spent_after, now_local(), lang)
    card = format_saved_card(result, status, lang, input_type, pace=pace)
    await update.message.reply_text(
        card, parse_mode="HTML", reply_markup=saved_card_keyboard(tx_id, lang)
    )


async def _analytics_payload(user_id: int, budget: float, lang: str, offset: int = 0):
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

    summary = await get_monthly_summary_for(user_id, month_first)

    sparkline = None
    prev_through = None
    ref = datetime(year, month, 1)
    if is_current:
        ref = now
        sparkline = await get_daily_spent_last_n(user_id, 7)
        pm = month - 1 or 12
        py = year if month > 1 else year - 1
        prev_through = await get_month_spent_through_day(user_id, py, pm, now.day)

    a = compute_analytics(
        summary, budget, ref,
        sparkline=sparkline, prev_through_day=prev_through, is_current=is_current,
    )
    text = format_analytics_card(a, lang)
    return text, analytics_keyboard(lang, offset)


# ───────────────────────── Commands ─────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        name = update.effective_user.first_name or ("друг" if lang == "ru" else "friend")
        logger.info("start telegram_id=%s", update.effective_user.id)
        await update.message.reply_text(
            t("welcome", lang, name=name),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(lang),
        )
    except Exception as e:
        logger.error("start_handler error: %s", e)
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
            t("budget_set", lang, amount=format_amount(amount)), parse_mode="HTML"
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
        status = await get_budget_status(user_id, budget=budget)
        from utils.formatters import _gauge_line
        await update.message.reply_text(
            t("budget_view", lang, amount=format_amount(budget), gauge=_gauge_line(status["percent"])),
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
        logger.info("report telegram_id=%s", user_id)

        summary = await get_monthly_summary(user_id)
        if not summary:
            await update.message.reply_text(t("empty_report", lang), parse_mode="HTML")
            return

        status = await get_budget_status(user_id, budget=_budget_of(user))
        await update.message.reply_text(
            format_monthly_report(summary, status, lang),
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
        text, kb = await _analytics_payload(user_id, _budget_of(user), lang, offset=0)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("analytics_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("history telegram_id=%s", user_id)

        transactions = await get_last_transactions(user_id, 10)
        if not transactions:
            await update.message.reply_text(t("empty_history", lang), parse_mode="HTML")
            return

        text = f"{format_history(transactions, lang)}\n\n<i>{t('history_hint', lang)}</i>"
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
        logger.info("tips telegram_id=%s", user_id)

        summary = await get_monthly_summary(user_id)
        if not summary:
            await update.message.reply_text(t("no_data_tips", lang), parse_mode="HTML")
            return

        await update.message.reply_text(t("tips_loading", lang), parse_mode="HTML")
        await _typing(update)
        tips = await get_savings_tips(summary, lang)
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
        logger.info("undo telegram_id=%s", user_id)

        deleted = await delete_last_transaction(user_id)
        if not deleted:
            await update.message.reply_text(t("undo_empty", lang), parse_mode="HTML")
            return

        emoji = CATEGORY_EMOJI.get(deleted.get("category", "Другое"), "📦")
        await update.message.reply_text(
            t("undo_done", lang, emoji=emoji, category=deleted.get("category", ""),
              amount=format_amount(deleted.get("amount", 0))),
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

        transactions = await get_last_transactions(user_id, 1000)
        n = len(transactions)
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
        data = query.data or ""
        logger.info("callback telegram_id=%s data=%s", user_id, data)

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
            text = f"{format_history(transactions, lang)}\n\n<i>{t('history_hint', lang)}</i>"
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
                      amount=format_amount(deleted.get("amount", 0))),
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
            text = f"{format_history(transactions, lang)}\n\n<i>{t('history_hint', lang)}</i>"
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
            text, kb = await _analytics_payload(user_id, budget, lang, offset=offset)
            await _safe_edit(query, text, parse_mode="HTML", reply_markup=kb)
            return

        # ── Analytics: open as a NEW message (from saved card / report / history) ──
        if data == "nav:analytics":
            await query.answer()
            text, kb = await _analytics_payload(user_id, budget, lang, offset=0)
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return

        # ── Open report (new message) ──
        if data == "nav:report":
            await query.answer()
            summary = await get_monthly_summary(user_id)
            if not summary:
                await query.message.reply_text(t("empty_report", lang), parse_mode="HTML")
                return
            status = await get_budget_status(user_id, budget=budget)
            await query.message.reply_text(
                format_monthly_report(summary, status, lang),
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
                format_monthly_report(summary, status, lang, month_label=label),
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
            tips = await get_savings_tips(summary, lang)
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
                query, t("budget_set", lang, amount=format_amount(amount)), parse_mode="HTML"
            )
            return

        # ── More menu actions ──
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
            transactions = await get_last_transactions(user_id, 1000)
            n = len(transactions)
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


# ───────────────────────── Menu button routing ─────────────────────────
# Reply-keyboard taps arrive as plain text — route them BEFORE the purchase parser.

BUTTON_ROUTES = {
    "📊 Аналитика": "analytics", "📊 Analytics": "analytics",
    "📋 История": "history", "📋 History": "history",
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
        route = BUTTON_ROUTES.get(label)
        if route:
            await _ROUTE_FUNCS[route](update, context)
            return

        user = await _get_user(update)
        lang = user.get("language", "ru")
        user_id = update.effective_user.id
        logger.info("text message telegram_id=%s", user_id)

        await _typing(update)
        user_context, budget, prior_spent = await _build_context(user, user_id)
        result = await parse_text_purchase(label, user_context)
        await _save_and_reply(update, result, lang, user_id, "text", budget, prior_spent)
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
        await _typing(update)
        user_context, budget, prior_spent = await _build_context(user, user_id)
        result = await parse_text_purchase(transcribed, user_context)
        await _save_and_reply(update, result, lang, user_id, "voice", budget, prior_spent)
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
        await _typing(update)
        user_context, budget, prior_spent = await _build_context(user, user_id)
        result = await parse_text_purchase(transcribed, user_context)
        await _save_and_reply(update, result, lang, user_id, "audio", budget, prior_spent)
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

        user_context, budget, prior_spent = await _build_context(user, user_id)
        result = await parse_photo_receipt(image_bytes, user_context)
        await _save_and_reply(update, result, lang, user_id, "photo", budget, prior_spent)
    except Exception as e:
        logger.error("photo_handler error: %s", e)
        await update.message.reply_text(t("error_generic", "ru"))


# Wire menu-button routes to handler functions (after definitions).
_ROUTE_FUNCS.update({
    "analytics": analytics_handler,
    "history": history_handler,
    "budget_view": budget_view_handler,
    "tips": tips_handler,
    "undo": undo_handler,
    "more": more_handler,
})


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("budget", budget_handler))
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
