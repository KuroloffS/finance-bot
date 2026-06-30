from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from services.currency_service import CURRENCIES
from utils.formatters import CATEGORY_EMOJI, format_amount, goal_progress


def webapp_keyboard(url: str, lang: str = "ru") -> InlineKeyboardMarkup:
    label = "Открыть Dayon" if lang == "ru" else "Open Dayon"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, web_app=WebAppInfo(url=url))]]
    )


def debts_keyboard(url: str, lang: str = "ru") -> InlineKeyboardMarkup:
    """Debts overview actions: add-via-chat + (optionally) open the Mini App."""
    add = "➕ Добавить долг" if lang == "ru" else "➕ Add a debt"
    rows = [[InlineKeyboardButton(add, callback_data="debt:new")]]
    if url:
        open_lbl = "📱 Открыть приложение" if lang == "ru" else "📱 Open the app"
        rows.append([InlineKeyboardButton(open_lbl, web_app=WebAppInfo(url=url))])
    return InlineKeyboardMarkup(rows)

# ───────────────────────── Menu button labels ─────────────────────────
# These exact strings are also matched in handlers.BUTTON_ROUTES.

MENU = {
    "ru": {
        "analytics": "📊 Аналитика",
        "history": "📋 История",
        "goals": "🎯 Цели",
        "budget": "💰 Бюджет",
        "tips": "💡 Советы",
        "undo": "↩️ Отменить",
        "more": "⚙️ Ещё",
    },
    "en": {
        "analytics": "📊 Analytics",
        "history": "📋 History",
        "goals": "🎯 Goals",
        "budget": "💰 Budget",
        "tips": "💡 Tips",
        "undo": "↩️ Undo",
        "more": "⚙️ More",
    },
}


def main_menu_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    m = MENU.get(lang, MENU["ru"])
    rows = [
        [KeyboardButton(m["analytics"]), KeyboardButton(m["history"])],
        [KeyboardButton(m["goals"]), KeyboardButton(m["budget"])],
        [KeyboardButton(m["tips"]), KeyboardButton(m["more"])],
    ]
    placeholder = "Напиши трату или выбери действие…" if lang == "ru" else "Type a purchase or pick an action…"
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder=placeholder,
    )


# ───────────────────────── Inline keyboards ─────────────────────────

def saved_card_keyboard(tx_id, lang: str = "ru") -> InlineKeyboardMarkup:
    undo = "↩️ Отменить" if lang == "ru" else "↩️ Undo"
    stats = "📊 Аналитика" if lang == "ru" else "📊 Analytics"
    row = []
    # Only offer Undo when we know the exact transaction id — never fall back to
    # "delete the latest", which could remove an unrelated newer purchase.
    if tx_id:
        row.append(InlineKeyboardButton(undo, callback_data=f"undo:{tx_id}"))
    row.append(InlineKeyboardButton(stats, callback_data="nav:analytics"))
    return InlineKeyboardMarkup([row])


def analytics_keyboard(lang: str = "ru", offset: int = 0) -> InlineKeyboardMarkup:
    if lang == "ru":
        report, tips, refresh = "📈 Отчёт", "💡 Советы", "🔄 Обновить"
        prev_lbl, cur_lbl = "🗓 Прошлый месяц", "📊 Текущий месяц"
    else:
        report, tips, refresh = "📈 Report", "💡 Tips", "🔄 Refresh"
        prev_lbl, cur_lbl = "🗓 Last month", "📊 Current month"

    # Month-nav button flips between "previous" and "back to current"
    if offset < 0:
        month_btn = InlineKeyboardButton(cur_lbl, callback_data="ana:0")
    else:
        month_btn = InlineKeyboardButton(prev_lbl, callback_data=f"ana:{offset - 1}")

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(report, callback_data="nav:report"),
             InlineKeyboardButton(tips, callback_data="nav:tips")],
            [month_btn, InlineKeyboardButton(refresh, callback_data=f"ana:{offset}")],
        ]
    )


def report_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    if lang == "ru":
        stats, prev = "📊 Аналитика", "🗓 Прошлый месяц"
    else:
        stats, prev = "📊 Analytics", "🗓 Last month"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(stats, callback_data="nav:analytics"),
          InlineKeyboardButton(prev, callback_data="rep:prev")]]
    )


def prev_report_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    if lang == "ru":
        stats, cur = "📊 Аналитика", "📅 Текущий месяц"
    else:
        stats, cur = "📊 Analytics", "📅 Current month"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(stats, callback_data="nav:analytics"),
          InlineKeyboardButton(cur, callback_data="nav:report")]]
    )


def history_delete_keyboard(transactions: list, lang: str) -> InlineKeyboardMarkup:
    """One 🗑 button per transaction + a footer nav row."""
    rows = []
    for tx in transactions:
        cat = tx.get("category", "Другое")
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        label = f"🗑 {emoji} {format_amount(tx.get('amount', 0))}"
        rows.append([InlineKeyboardButton(label, callback_data=f"del:{tx['id']}")])

    if lang == "ru":
        refresh, stats = "🔄 Обновить", "📊 Аналитика"
    else:
        refresh, stats = "🔄 Refresh", "📊 Analytics"
    rows.append(
        [InlineKeyboardButton(refresh, callback_data="hist:refresh"),
         InlineKeyboardButton(stats, callback_data="nav:analytics")]
    )
    return InlineKeyboardMarkup(rows)


def budget_presets_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    custom = "✏️ Своя сумма" if lang == "ru" else "✏️ Custom"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("3 млн", callback_data="bud:3000000"),
             InlineKeyboardButton("5 млн", callback_data="bud:5000000"),
             InlineKeyboardButton("8 млн", callback_data="bud:8000000")],
            [InlineKeyboardButton(custom, callback_data="bud:custom")],
        ]
    )


def more_menu_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    if lang == "ru":
        cur, notif = "💱 Валюта", "🔔 Уведомления"
        langtg, reset, help_ = "🌐 RU / EN", "🗑 Сбросить всё", "ℹ️ Помощь"
    else:
        cur, notif = "💱 Currency", "🔔 Notifications"
        langtg, reset, help_ = "🌐 RU / EN", "🗑 Reset all", "ℹ️ Help"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(cur, callback_data="more:currency"),
             InlineKeyboardButton(notif, callback_data="more:settings")],
            [InlineKeyboardButton(langtg, callback_data="lang:toggle")],
            [InlineKeyboardButton(reset, callback_data="reset:ask")],
            [InlineKeyboardButton(help_, callback_data="more:help")],
        ]
    )


# ───────────────────────── Savings goals ─────────────────────────

def _trunc(s: str, n: int = 22) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def goals_list_keyboard(goals: list, lang: str = "ru") -> InlineKeyboardMarkup:
    """One button per goal (emoji · title · progress) + a 'new goal' button."""
    rows = []
    for g in goals:
        p = goal_progress(g)
        badge = "🏆" if p["done"] else f"{p['percent']:.0f}%"
        emoji = g.get("emoji") or "🎯"
        label = f"{emoji} {_trunc(g.get('title', ''))} · {badge}"
        rows.append([InlineKeyboardButton(label, callback_data=f"goal:open:{g['id']}")])
    new_lbl = "➕ Новая цель" if lang == "ru" else "➕ New goal"
    rows.append([InlineKeyboardButton(new_lbl, callback_data="goal:new")])
    return InlineKeyboardMarkup(rows)


def goal_detail_keyboard(goal: dict, lang: str = "ru") -> InlineKeyboardMarkup:
    p = goal_progress(goal)
    gid = goal["id"]
    if lang == "ru":
        add, fill = "💰 Пополнить", "🎉 Закрыть цель"
        dl, dele, back = "✏️ Срок", "🗑 Удалить", "‹ К целям"
    else:
        add, fill = "💰 Top up", "🎉 Complete it"
        dl, dele, back = "✏️ Deadline", "🗑 Delete", "‹ Goals"
    rows = [[InlineKeyboardButton(add, callback_data=f"goal:add:{gid}")]]
    if not p["done"] and p["remaining"] > 0:
        rows.append([InlineKeyboardButton(fill, callback_data=f"goal:fill:{gid}")])
    rows.append([
        InlineKeyboardButton(dl, callback_data=f"goal:editdl:{gid}"),
        InlineKeyboardButton(dele, callback_data=f"goal:del:{gid}"),
    ])
    rows.append([InlineKeyboardButton(back, callback_data="goal:list")])
    return InlineKeyboardMarkup(rows)


def goal_delete_confirm_keyboard(goal_id, lang: str = "ru") -> InlineKeyboardMarkup:
    if lang == "ru":
        yes, no = "✅ Да, удалить", "❌ Отмена"
    else:
        yes, no = "✅ Yes, delete", "❌ Cancel"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(yes, callback_data=f"goal:delyes:{goal_id}")],
        [InlineKeyboardButton(no, callback_data=f"goal:open:{goal_id}")],
    ])


# ───────────────────────── Currency picker ─────────────────────────

def currency_keyboard(current: str = "UZS", lang: str = "ru") -> InlineKeyboardMarkup:
    """Grid of supported currencies; the active one gets a ✓."""
    rows, row = [], []
    for code, meta in CURRENCIES.items():
        mark = "✓ " if code == current else ""
        label = f"{mark}{meta['flag']} {code}"
        row.append(InlineKeyboardButton(label, callback_data=f"cur:{code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


# ───────────────────────── Notification settings ─────────────────────────

# Display order + labels for the toggle list. Keys match DEFAULT_NOTIFY.
NOTIFY_ORDER = ["budget_alerts", "large_tx", "weekly_summary", "daily_digest", "goal_reminders", "debt_reminders", "payment_reminders"]
NOTIFY_LABELS = {
    "ru": {
        "budget_alerts": "Бюджет: 80% и 100%",
        "large_tx": "Крупные траты",
        "weekly_summary": "Итоги недели",
        "daily_digest": "Итоги дня",
        "goal_reminders": "Напоминания о целях",
        "debt_reminders": "Напоминания о долгах",
        "payment_reminders": "Напоминания о платежах",
    },
    "en": {
        "budget_alerts": "Budget: 80% & 100%",
        "large_tx": "Large purchases",
        "weekly_summary": "Weekly summary",
        "daily_digest": "Daily wrap-up",
        "goal_reminders": "Goal reminders",
        "debt_reminders": "Debt reminders",
        "payment_reminders": "Payment reminders",
    },
}


def settings_keyboard(settings: dict, lang: str = "ru") -> InlineKeyboardMarkup:
    labels = NOTIFY_LABELS.get(lang, NOTIFY_LABELS["ru"])
    rows = []
    for key in NOTIFY_ORDER:
        on = bool(settings.get(key))
        mark = "✅" if on else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {labels[key]}", callback_data=f"nset:{key}")])
    return InlineKeyboardMarkup(rows)


def reset_confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        yes, no = "✅ Да, удалить всё", "❌ Отмена"
    else:
        yes, no = "✅ Yes, delete all", "❌ Cancel"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(yes, callback_data="reset:yes")],
            [InlineKeyboardButton(no, callback_data="reset:no")],
        ]
    )
