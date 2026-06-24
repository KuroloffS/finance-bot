from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from utils.formatters import CATEGORY_EMOJI, format_amount

# ───────────────────────── Menu button labels ─────────────────────────
# These exact strings are also matched in handlers.BUTTON_ROUTES.

MENU = {
    "ru": {
        "analytics": "📊 Аналитика",
        "history": "📋 История",
        "budget": "💰 Бюджет",
        "tips": "💡 Советы",
        "undo": "↩️ Отменить",
        "more": "⚙️ Ещё",
    },
    "en": {
        "analytics": "📊 Analytics",
        "history": "📋 History",
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
        [KeyboardButton(m["budget"]), KeyboardButton(m["tips"])],
        [KeyboardButton(m["undo"]), KeyboardButton(m["more"])],
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
        langtg, reset, help_ = "🌐 RU / EN", "🗑 Сбросить всё", "ℹ️ Помощь"
    else:
        langtg, reset, help_ = "🌐 RU / EN", "🗑 Reset all", "ℹ️ Help"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(langtg, callback_data="lang:toggle")],
            [InlineKeyboardButton(reset, callback_data="reset:ask")],
            [InlineKeyboardButton(help_, callback_data="more:help")],
        ]
    )


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
