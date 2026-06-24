import calendar
from datetime import datetime
from html import escape

CATEGORY_EMOJI = {
    "Продукты": "🛒",
    "Кафе и рестораны": "🍽️",
    "Транспорт": "🚗",
    "Жильё и коммуналка": "🏠",
    "Здоровье": "💊",
    "Развлечения": "🎬",
    "Шоппинг": "🛍️",
    "Работа и бизнес": "💼",
    "Другое": "📦",
}

INPUT_EMOJI = {"text": "⌨️", "voice": "🎤", "photo": "📸", "audio": "🎧"}

_MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
    7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}
# Genitive (for "за <месяц>") and short prev-month forms
_MONTHS_RU_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}
_MONTHS_RU_SHORT = {
    1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр", 5: "Май", 6: "Июн",
    7: "Июл", 8: "Авг", 9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек",
}
# Accusative weekday ("пик в субботу")
_WEEKDAYS_RU_ACC = {
    0: "в понедельник", 1: "во вторник", 2: "в среду", 3: "в четверг",
    4: "в пятницу", 5: "в субботу", 6: "в воскресенье",
}
_WEEKDAYS_EN = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}

DIVIDER = "━━━━━━━━━━━━━━━━━━━"
_DIVIDER = DIVIDER  # backward-compat alias

_SPARK = "▁▂▃▄▅▆▇█"


def _f(amount) -> str:
    """125000 → '125 000 сум'"""
    try:
        return f"{int(round(float(amount))):,} сум".replace(",", " ")
    except (TypeError, ValueError):
        return "0 сум"


def format_amount(amount) -> str:
    return _f(amount)


def _plural_ru(n: int, forms: tuple) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return forms[1]
    return forms[2]


def _count_label(n: int, lang: str) -> str:
    if lang == "ru":
        return f"{n} {_plural_ru(n, ('трата', 'траты', 'трат'))}"
    return f"{n} tx" if n != 1 else "1 tx"


def count_label(n: int, lang: str) -> str:
    """Public: '5 трат' / '5 tx' with correct Russian plural."""
    return _count_label(n, lang)


def _month_name_for(year: int, month: int, lang: str) -> str:
    if lang == "ru":
        return f"{_MONTHS_RU[month]} {year}"
    return datetime(year, month, 1).strftime("%B %Y")


def _month_name(lang: str) -> str:
    now = datetime.now()
    return _month_name_for(now.year, now.month, lang)


def _prev_month_short(year: int, month: int, lang: str) -> str:
    pm = month - 1 or 12
    return _MONTHS_RU_SHORT[pm] if lang == "ru" else datetime(year, pm, 1).strftime("%b")


# ───────────────────────── Visual primitives ─────────────────────────

def zone_dot(percent: float) -> str:
    """Traffic-light dot by budget usage."""
    p = float(percent or 0)
    if p < 60:
        return "🟢"
    if p <= 85:
        return "🟡"
    return "🔴"


def format_progress_bar(percent: float, length: int = 16) -> str:
    """█████░░░░░░░░░░░ 31.2%"""
    pct = max(0.0, float(percent))
    filled = min(length, int(round(length * min(pct, 100) / 100)))
    bar = "█" * filled + "░" * (length - filled)
    return f"{bar} {pct:.1f}%"


def _bar(percent: float, length: int = 10) -> str:
    pct = max(0.0, min(100.0, float(percent)))
    filled = min(length, int(round(length * pct / 100)))
    return "█" * filled + "░" * (length - filled)


def _gauge_line(percent: float) -> str:
    """🟡 <code>██████████░░░░░░ 65.7%</code>"""
    return f"{zone_dot(percent)} <code>{format_progress_bar(percent)}</code>"


def delta_chip(pct, lang: str = "ru") -> str:
    """Spending momentum chip. NOTE inversion: lower spend is GOOD (green)."""
    if pct is None:
        return ""
    p = float(pct)
    if abs(p) <= 3:
        return "▪️ ровно" if lang == "ru" else "▪️ flat"
    if p < 0:
        return f"🟢 ↓ {abs(p):.0f}%"
    return f"🔴 ↑ {abs(p):.0f}%"


def render_sparkline(buckets: list) -> str:
    if not buckets:
        return ""
    mx = max(buckets)
    if mx <= 0:
        return _SPARK[0] * len(buckets)
    out = []
    for v in buckets:
        idx = int(round((float(v) / mx) * (len(_SPARK) - 1)))
        out.append(_SPARK[max(0, min(len(_SPARK) - 1, idx))])
    return "".join(out)


# ───────────────────────── Budget / cards ─────────────────────────

def format_budget_status(status: dict, lang: str, pace: str | None = None) -> str:
    head = "💰 <b>Бюджет</b>" if lang == "ru" else "💰 <b>Budget</b>"
    line2 = f"{_f(status['spent'])} / {_f(status['budget'])}"
    lines = [head, _gauge_line(status["percent"]), line2]

    if status["warning"]:
        if status["percent"] >= 100:
            msg = "🚨 Бюджет исчерпан!" if lang == "ru" else "🚨 Budget exceeded!"
        else:
            msg = "⚠️ Потрачено больше 80% бюджета" if lang == "ru" else "⚠️ Over 80% of budget used"
        lines.append(msg)
    else:
        msg = (
            f"✅ Остаётся {_f(status['remaining'])}"
            if lang == "ru"
            else f"✅ {_f(status['remaining'])} left"
        )
        lines.append(msg)

    if pace:
        lines.append(f"<i>{pace}</i>")
    return "\n".join(lines)


def format_saved_card(
    result: dict, status: dict, lang: str, input_type: str = "text", pace: str | None = None
) -> str:
    cat = result.get("category", "Другое")
    emoji = CATEGORY_EMOJI.get(cat, "📦")
    src = INPUT_EMOJI.get(input_type, "")

    title = "✅ <b>Записал трату</b>" if lang == "ru" else "✅ <b>Saved</b>"
    if src:
        title = f"{title} {src}"

    lines = [
        title,
        DIVIDER,
        "",
        f"{emoji} <b>{escape(cat)}</b>",
        f"💸 <b>{_f(result.get('amount'))}</b>",
    ]

    merchant = result.get("merchant")
    if merchant:
        lines.append(f"🏪 {escape(str(merchant))}")

    desc = result.get("description")
    if desc and str(desc).strip() and str(desc).strip().lower() != cat.lower():
        lines.append(f"📝 {escape(str(desc))}")

    advice = result.get("advice")
    if advice and str(advice).strip():
        label = "💡 <i>Совет:</i>" if lang == "ru" else "💡 <i>Tip:</i>"
        lines.append("")
        lines.append(f"<blockquote>{label} {escape(str(advice))}</blockquote>")

    lines.append("")
    lines.append(DIVIDER)
    lines.append(format_budget_status(status, lang, pace=pace))
    return "\n".join(lines)


def format_monthly_report(summary: list, status: dict, lang: str, month_label: str | None = None) -> str:
    label = month_label or _month_name(lang)
    title = (
        f"📊 <b>Отчёт за {label}</b>" if lang == "ru" else f"📊 <b>Report — {label}</b>"
    )
    lines = [title, DIVIDER, ""]

    total_spent = sum(float(r.get("total_spent", 0)) for r in summary) or 1.0

    for row in sorted(summary, key=lambda x: float(x.get("total_spent", 0)), reverse=True):
        cat = row.get("category", "Другое")
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        amt = float(row.get("total_spent", 0))
        share = amt / total_spent * 100
        n = int(row.get("num_transactions", 0))
        lines.append(f"{emoji} <b>{escape(cat)}</b>")
        lines.append(f"   {_f(amt)} · {share:.0f}% · {_count_label(n, lang)}")
        lines.append(f"   <code>{_bar(share, 15)}</code>")
        lines.append("")

    lines.append(DIVIDER)
    lines.append(format_budget_status(status, lang))
    return "\n".join(lines)


def format_history(transactions: list, lang: str) -> str:
    header = "📋 <b>Последние траты</b>" if lang == "ru" else "📋 <b>Recent transactions</b>"
    lines = [header, DIVIDER, ""]

    for tx in transactions:
        cat = tx.get("category", "Другое")
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        src = INPUT_EMOJI.get(tx.get("input_type", "text"), "")
        date_str = str(tx.get("purchase_date", ""))[:10]
        amt = _f(tx.get("amount", 0))
        merchant = tx.get("merchant")
        tail = f" · {escape(str(merchant))}" if merchant else ""
        lines.append(f"{emoji} <b>{amt}</b> {src}")
        lines.append(f"   <i>{escape(cat)}{tail}</i> · {date_str}")
        lines.append("")

    return "\n".join(lines).rstrip()


def format_tips(tips_text: str, lang: str) -> str:
    header = "💡 <b>Советы по экономии</b>" if lang == "ru" else "💡 <b>Savings tips</b>"
    return f"{header}\n{DIVIDER}\n\n{escape(tips_text)}"


# ───────────────────────── Analytics ─────────────────────────

def compute_analytics(
    summary: list,
    budget: float,
    now: datetime,
    *,
    sparkline: list | None = None,
    prev_through_day: float | None = None,
    is_current: bool = True,
) -> dict:
    """Pure arithmetic — no DB, no I/O. Builds everything the analytics card renders."""
    budget = float(budget or 0)
    spent = sum(float(r.get("total_spent", 0)) for r in summary)
    n_total = sum(int(r.get("num_transactions", 0)) for r in summary)
    avg_ticket = spent / n_total if n_total else 0.0

    remaining = max(0.0, budget - spent)
    percent = (spent / budget * 100) if budget > 0 else 0.0

    year, month = now.year, now.month
    days_in_month = calendar.monthrange(year, month)[1]
    day = now.day if is_current else days_in_month
    days_left = max(0, days_in_month - day)

    burn = spent / day if day else 0.0
    safe_daily = (remaining / days_left) if days_left > 0 else 0.0
    projection = (spent / day * days_in_month) if (is_current and day) else spent
    proj_delta = projection - budget
    proj_pct = (projection / budget * 100) if budget > 0 else 0.0

    mom_delta_pct = None
    if prev_through_day is not None and prev_through_day > 0:
        mom_delta_pct = (spent - prev_through_day) / prev_through_day * 100

    total_for_share = spent or 1.0
    top = sorted(summary, key=lambda x: float(x.get("total_spent", 0)), reverse=True)[:3]
    top3 = [
        {
            "category": r.get("category", "Другое"),
            "amount": float(r.get("total_spent", 0)),
            "share": float(r.get("total_spent", 0)) / total_for_share * 100,
            "n": int(r.get("num_transactions", 0)),
        }
        for r in top
    ]

    peak_weekday = None
    if sparkline and max(sparkline) > 0:
        peak_idx = max(range(len(sparkline)), key=lambda i: sparkline[i])
        # bucket i corresponds to date (today - (n-1-i))
        from datetime import timedelta
        peak_date = now.date() - timedelta(days=(len(sparkline) - 1 - peak_idx))
        peak_weekday = peak_date.weekday()

    return {
        "spent": spent, "budget": budget, "remaining": remaining,
        "percent": percent, "warning": percent >= 80,
        "n_total": n_total, "avg_ticket": avg_ticket,
        "day": day, "days_in_month": days_in_month, "days_left": days_left,
        "burn": burn, "safe_daily": safe_daily,
        "projection": projection, "proj_delta": proj_delta, "proj_pct": proj_pct,
        "mom_delta_pct": mom_delta_pct, "prev_total": prev_through_day,
        "sparkline": sparkline, "peak_weekday": peak_weekday,
        "top3": top3, "is_current": is_current,
        "year": year, "month": month,
    }


def format_analytics_card(a: dict, lang: str, month_label: str | None = None) -> str:
    label = month_label or _month_name_for(a["year"], a["month"], lang)
    title = (
        f"📊 <b>Аналитика — {label}</b>" if lang == "ru" else f"📊 <b>Analytics — {label}</b>"
    )
    lines = [title, DIVIDER, ""]

    # Empty state — still render the frame
    if not a["top3"] and a["spent"] <= 0:
        empty = "— пока нет трат —" if lang == "ru" else "— no transactions yet —"
        lines.append(f"<i>{empty}</i>")
        lines.append("")
        lines.append(DIVIDER)
        of = "из" if lang == "ru" else "of"
        lines.append(f"💸 <b>0 сум</b>  <i>{of} {_f(a['budget'])}</i>")
        lines.append(_gauge_line(0))
        return "\n".join(lines)

    of = "из" if lang == "ru" else "of"
    lines.append(f"💸 <b>{_f(a['spent'])}</b>  <i>{of} {_f(a['budget'])}</i>")
    lines.append(_gauge_line(a["percent"]))

    # MoM chip
    if a["mom_delta_pct"] is not None:
        chip = delta_chip(a["mom_delta_pct"], lang)
        prev_lbl = _prev_month_short(a["year"], a["month"], lang)
        was = "было" if lang == "ru" else "was"
        lines.append(f"🔁 vs {prev_lbl}: {chip}  <i>({was} {_f(a['prev_total'])})</i>")

    # Forecast (current month only)
    if a["is_current"]:
        lines.append("")
        fc_head = "🔮 <b>Прогноз к концу месяца</b>" if lang == "ru" else "🔮 <b>Month-end forecast</b>"
        lines.append(fc_head)
        if a["proj_delta"] <= 0:
            verdict = (
                f"✅ уложишься (−{_f(abs(a['proj_delta']))})"
                if lang == "ru"
                else f"✅ on track (−{_f(abs(a['proj_delta']))})"
            )
        else:
            verdict = (
                f"⚠️ перерасход (+{_f(a['proj_delta'])})"
                if lang == "ru"
                else f"⚠️ overspend (+{_f(a['proj_delta'])})"
            )
        budget_word = "бюджета" if lang == "ru" else "of budget"
        lines.append(
            f"<blockquote>≈ {_f(a['projection'])} · {a['proj_pct']:.0f}% {budget_word}\n{verdict}</blockquote>"
        )

        # Pace
        lines.append("")
        if lang == "ru":
            lines.append(f"🔥 Темп: <b>{_f(a['burn'])}/день</b>")
            if a["days_left"] > 0:
                lines.append(
                    f"🛟 Норма: <b>≤ {_f(a['safe_daily'])}/день</b>  <i>(осталось {a['days_left']} дн.)</i>"
                )
        else:
            lines.append(f"🔥 Pace: <b>{_f(a['burn'])}/day</b>")
            if a["days_left"] > 0:
                lines.append(
                    f"🛟 Safe: <b>≤ {_f(a['safe_daily'])}/day</b>  <i>({a['days_left']} days left)</i>"
                )

    # Sparkline
    if a.get("sparkline") and max(a["sparkline"]) > 0:
        lines.append("")
        spark_head = "📈 <b>Расходы, 7 дней</b>" if lang == "ru" else "📈 <b>Last 7 days</b>"
        lines.append(spark_head)
        peak = ""
        wd = a.get("peak_weekday")
        if wd is not None:
            if lang == "ru":
                peak = f"  <i>пик {_WEEKDAYS_RU_ACC.get(wd, '')}</i>"
            else:
                peak = f"  <i>peak on {_WEEKDAYS_EN.get(wd, '')}</i>"
        lines.append(f"<code>{render_sparkline(a['sparkline'])}</code>{peak}")

    # Top categories
    lines.append("")
    lines.append(DIVIDER)
    where = "🏆 <b>Куда уходят деньги</b>" if lang == "ru" else "🏆 <b>Where money goes</b>"
    lines.append(where)
    lines.append("")
    for c in a["top3"]:
        emoji = CATEGORY_EMOJI.get(c["category"], "📦")
        lines.append(
            f"{emoji} <b>{escape(c['category'])}</b>  <code>{_bar(c['share'], 10)}</code> {c['share']:.0f}%"
        )
        lines.append(f"   {_f(c['amount'])} · {_count_label(c['n'], lang)}")

    # Footer
    lines.append("")
    lines.append(DIVIDER)
    if lang == "ru":
        lines.append(f"🧾 {_count_label(a['n_total'], lang)} · средний чек {_f(a['avg_ticket'])}")
    else:
        lines.append(f"🧾 {_count_label(a['n_total'], lang)} · avg {_f(a['avg_ticket'])}")

    return "\n".join(lines)


def build_saved_pace(budget: float, spent_after: float, now: datetime, lang: str) -> str:
    """Short italic pace line for the saved-card footer (no DB)."""
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day = max(1, now.day)
    days_left = max(0, days_in_month - day)
    burn = spent_after / day
    remaining = max(0.0, float(budget) - spent_after)
    safe = (remaining / days_left) if days_left > 0 else 0.0
    if lang == "ru":
        if days_left > 0:
            return f"🔥 темп {_f(burn)}/день · 🛟 норма ≤ {_f(safe)}/день"
        return f"🔥 темп {_f(burn)}/день"
    if days_left > 0:
        return f"🔥 pace {_f(burn)}/day · 🛟 safe ≤ {_f(safe)}/day"
    return f"🔥 pace {_f(burn)}/day"
