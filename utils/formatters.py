import calendar
from datetime import date, datetime, timedelta
from html import escape

from services.currency_service import DEFAULT_CURRENCY, currency_meta

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

INPUT_EMOJI = {"text": "⌨️", "voice": "🎤", "photo": "📸", "audio": "🎧", "app": "📱"}

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

_SPARK = "▁▂▃▄▅▆▇█"


# ───────────────────────── Money ─────────────────────────

def _f(amount, currency: str = DEFAULT_CURRENCY) -> str:
    """Currency-aware money formatter.
    125000 → '125 000 сум' · 1234.5 (USD) → '$1,234.50' · 91000 (RUB) → '91 000 ₽'
    """
    meta = currency_meta(currency)
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return f"0 {meta['symbol']}" if meta["pos"] == "suffix" else f"{meta['symbol']}0"
    neg = val < 0
    val = abs(val)
    dec = meta["dec"]
    raw = f"{val:,.{dec}f}"  # ',' thousands, '.' decimal
    if dec > 0:
        int_part, _, frac = raw.partition(".")
        body = int_part.replace(",", meta["grp"]) + "." + frac
    else:
        body = raw.replace(",", meta["grp"])
    sym = meta["symbol"]
    out = f"{sym}{body}" if meta["pos"] == "prefix" else f"{body} {sym}"
    return ("−" + out) if neg else out


def format_amount(amount, currency: str = DEFAULT_CURRENCY) -> str:
    return _f(amount, currency)


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


def _days_label(n: int, lang: str) -> str:
    n = abs(int(n))
    if lang == "ru":
        return f"{n} {_plural_ru(n, ('день', 'дня', 'дней'))}"
    return f"{n} day" if n == 1 else f"{n} days"


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


def _short_date(d: date, lang: str) -> str:
    """'1 дек' / 'Dec 1' (Windows-safe — no %-d)."""
    if lang == "ru":
        return f"{d.day} {_MONTHS_RU_GEN[d.month][:3]}"
    return f"{d.strftime('%b')} {d.day}"


# ───────────────────────── Visual primitives ─────────────────────────

def zone_dot(percent: float) -> str:
    """Traffic-light dot by budget usage."""
    p = float(percent or 0)
    if p < 60:
        return "🟢"
    if p <= 85:
        return "🟡"
    return "🔴"


def goal_dot(percent: float) -> str:
    """For goals, MORE progress is better → invert the colour ramp."""
    p = float(percent or 0)
    if p >= 100:
        return "🏆"
    if p >= 66:
        return "🟢"
    if p >= 33:
        return "🟡"
    return "🔵"


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

def format_budget_status(status: dict, lang: str, currency: str = DEFAULT_CURRENCY) -> str:
    head = "💰 <b>Бюджет</b>" if lang == "ru" else "💰 <b>Budget</b>"
    line2 = f"{_f(status['spent'], currency)} / {_f(status['budget'], currency)}"
    lines = [head, f"{zone_dot(status['percent'])} {status['percent']:.1f}%", line2]

    if status["warning"]:
        if status["percent"] >= 100:
            msg = "🚨 Бюджет исчерпан!" if lang == "ru" else "🚨 Budget exceeded!"
        else:
            msg = "⚠️ Потрачено больше 80% бюджета" if lang == "ru" else "⚠️ Over 80% of budget used"
        lines.append(msg)
    else:
        msg = (
            f"✅ Остаётся {_f(status['remaining'], currency)}"
            if lang == "ru"
            else f"✅ {_f(status['remaining'], currency)} left"
        )
        lines.append(msg)

    return "\n".join(lines)


def format_saved_card(
    result: dict, status: dict, lang: str, input_type: str = "text",
    currency: str = DEFAULT_CURRENCY, fx_note: str | None = None,
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
        f"💸 <b>{_f(result.get('amount'), currency)}</b>",
    ]

    if fx_note:
        lines.append(f"💱 <i>{escape(fx_note)}</i>")

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
    lines.append(format_budget_status(status, lang, currency=currency))
    return "\n".join(lines)


def format_monthly_report(summary: list, status: dict, lang: str, month_label: str | None = None, currency: str = DEFAULT_CURRENCY) -> str:
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
        lines.append(f"   {_f(amt, currency)} · {share:.0f}% · {_count_label(n, lang)}")
        lines.append("")

    lines.append(DIVIDER)
    lines.append(format_budget_status(status, lang, currency=currency))
    return "\n".join(lines)


def format_history(transactions: list, lang: str, currency: str = DEFAULT_CURRENCY) -> str:
    header = "📋 <b>Последние траты</b>" if lang == "ru" else "📋 <b>Recent transactions</b>"
    lines = [header, DIVIDER, ""]

    for tx in transactions:
        cat = tx.get("category", "Другое")
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        src = INPUT_EMOJI.get(tx.get("input_type", "text"), "")
        date_str = str(tx.get("purchase_date", ""))[:10]
        amt = _f(tx.get("amount", 0), currency)
        # If entered in another currency, show the original in parentheses.
        oc = tx.get("original_currency")
        oa = tx.get("original_amount")
        fx = ""
        if oc and oc != currency and oa:
            fx = f" <i>(≈ {_f(oa, oc)})</i>"
        merchant = tx.get("merchant")
        tail = f" · {escape(str(merchant))}" if merchant else ""
        lines.append(f"{emoji} <b>{amt}</b>{fx} {src}")
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


def format_analytics_card(a: dict, lang: str, month_label: str | None = None, currency: str = DEFAULT_CURRENCY) -> str:
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
        lines.append(f"💸 <b>{_f(0, currency)}</b>  <i>{of} {_f(a['budget'], currency)}</i>")
        lines.append(f"{zone_dot(0)} 0.0%")
        return "\n".join(lines)

    of = "из" if lang == "ru" else "of"
    lines.append(f"💸 <b>{_f(a['spent'], currency)}</b>  <i>{of} {_f(a['budget'], currency)}</i>")
    lines.append(f"{zone_dot(a['percent'])} {a['percent']:.1f}%")

    # MoM chip
    if a["mom_delta_pct"] is not None:
        chip = delta_chip(a["mom_delta_pct"], lang)
        prev_lbl = _prev_month_short(a["year"], a["month"], lang)
        was = "было" if lang == "ru" else "was"
        lines.append(f"🔁 vs {prev_lbl}: {chip}  <i>({was} {_f(a['prev_total'], currency)})</i>")

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
            f"{emoji} <b>{escape(c['category'])}</b>  ·  {c['share']:.0f}%"
        )
        lines.append(f"   {_f(c['amount'], currency)} · {_count_label(c['n'], lang)}")

    # Footer
    lines.append("")
    lines.append(DIVIDER)
    if lang == "ru":
        lines.append(f"🧾 {_count_label(a['n_total'], lang)} · средний чек {_f(a['avg_ticket'], currency)}")
    else:
        lines.append(f"🧾 {_count_label(a['n_total'], lang)} · avg {_f(a['avg_ticket'], currency)}")

    return "\n".join(lines)


# ───────────────────────── Savings goals ─────────────────────────

def goal_progress(goal: dict, today: date | None = None) -> dict:
    """Pure arithmetic for a single goal — percent, remaining, deadline pace."""
    target = float(goal.get("target_amount", 0) or 0)
    saved = float(goal.get("saved_amount", 0) or 0)
    pct = (saved / target * 100) if target > 0 else 0.0
    pct = max(0.0, pct)
    remaining = max(0.0, target - saved)
    done = target > 0 and saved >= target

    days_left = None
    per_day = None
    overdue = False
    deadline = goal.get("deadline")
    if deadline:
        try:
            dl = date.fromisoformat(str(deadline)[:10])
            ref = today or date.today()
            days_left = (dl - ref).days
            if days_left < 0:
                overdue = True
            elif days_left > 0 and remaining > 0:
                per_day = remaining / days_left
        except (ValueError, TypeError):
            pass

    return {
        "target": target, "saved": saved, "remaining": remaining,
        "percent": pct, "done": done, "deadline": deadline,
        "days_left": days_left, "per_day": per_day, "overdue": overdue,
    }


def format_goal_card(goal: dict, lang: str, today: date | None = None) -> str:
    """Detailed single-goal card."""
    cur = goal.get("currency") or DEFAULT_CURRENCY
    p = goal_progress(goal, today)
    emoji = goal.get("emoji") or "🎯"
    title = escape(str(goal.get("title", "")))
    pct = p["percent"]

    head = f"{emoji} <b>{title}</b>"
    lines = [head, DIVIDER, ""]

    if p["done"]:
        done_msg = "🏆 <b>Цель достигнута!</b>" if lang == "ru" else "🏆 <b>Goal reached!</b>"
        lines.append(done_msg)
        lines.append("")
    lines.append(f"{goal_dot(pct)} <b>{min(pct, 100):.0f}%</b>")
    lines.append(f"<b>{_f(p['saved'], cur)}</b> / {_f(p['target'], cur)}")

    if not p["done"]:
        left_lbl = "Осталось накопить" if lang == "ru" else "Left to save"
        lines.append(f"{left_lbl}: <b>{_f(p['remaining'], cur)}</b>")

    # Deadline line
    if p["deadline"]:
        try:
            dl = date.fromisoformat(str(p["deadline"])[:10])
            dl_str = _short_date(dl, lang)
        except (ValueError, TypeError):
            dl_str = str(p["deadline"])
        if p["overdue"] and not p["done"]:
            lines.append("")
            msg = f"📅 Срок ({dl_str}) прошёл" if lang == "ru" else f"📅 Deadline ({dl_str}) passed"
            lines.append(msg)
        elif p["days_left"] is not None:
            lines.append("")
            until = "до" if lang == "ru" else "by"
            dleft = _days_label(p["days_left"], lang)
            tail = f"осталось {dleft}" if lang == "ru" else f"{dleft} left"
            lines.append(f"📅 {until} {dl_str} · {tail}")
            if p["per_day"] and not p["done"]:
                if lang == "ru":
                    lines.append(f"💪 Чтобы успеть: ≈ <b>{_f(p['per_day'], cur)}/день</b>")
                else:
                    lines.append(f"💪 To make it: ≈ <b>{_f(p['per_day'], cur)}/day</b>")
    return "\n".join(lines)


def format_goals_list(goals: list, lang: str, today: date | None = None) -> str:
    """Compact list of all active goals with mini progress bars."""
    header = "🎯 <b>Мои цели</b>" if lang == "ru" else "🎯 <b>My goals</b>"
    if not goals:
        empty = (
            "📭 Пока нет целей.\nСоздай первую — и я помогу накопить 🙂"
            if lang == "ru"
            else "📭 No goals yet.\nCreate one and I'll help you save 🙂"
        )
        return f"{header}\n{DIVIDER}\n\n{empty}"

    lines = [header, DIVIDER, ""]
    for g in goals:
        cur = g.get("currency") or DEFAULT_CURRENCY
        p = goal_progress(g, today)
        emoji = g.get("emoji") or "🎯"
        title = escape(str(g.get("title", "")))
        badge = "🏆" if p["done"] else f"{p['percent']:.0f}%"
        lines.append(f"{emoji} <b>{title}</b>  ·  {badge}")
        sub = f"{_f(p['saved'], cur)} / {_f(p['target'], cur)}"
        if not p["done"] and p["days_left"] is not None and p["days_left"] >= 0:
            sub += f" · {_days_label(p['days_left'], lang)}" if lang == "en" else f" · ещё {_days_label(p['days_left'], lang)}"
        lines.append(f"<i>{sub}</i>")
        lines.append("")
    return "\n".join(lines).rstrip()


# ───────────────────────── Notifications ─────────────────────────

def format_budget_alert(status: dict, lang: str, currency: str = DEFAULT_CURRENCY) -> str:
    """Real-time alert when a budget threshold (80% / 100%) is newly crossed."""
    pct = status["percent"]
    if pct >= 100:
        head = "🚨 <b>Бюджет исчерпан</b>" if lang == "ru" else "🚨 <b>Budget used up</b>"
        if lang == "ru":
            body = f"Ты потратил {_f(status['spent'], currency)} из {_f(status['budget'], currency)} — это {pct:.0f}% бюджета."
            tail = "Дальше — только в плюс к перерасходу. Может, притормозить? 🤝"
        else:
            body = f"You've spent {_f(status['spent'], currency)} of {_f(status['budget'], currency)} — that's {pct:.0f}% of budget."
            tail = "Anything more adds to the overspend. Maybe ease off? 🤝"
    else:
        head = "⚠️ <b>80% бюджета позади</b>" if lang == "ru" else "⚠️ <b>80% of budget spent</b>"
        if lang == "ru":
            body = f"Потрачено {_f(status['spent'], currency)} из {_f(status['budget'], currency)} ({pct:.0f}%)."
            tail = f"Остаётся {_f(status['remaining'], currency)}. Держим темп 👀"
        else:
            body = f"Spent {_f(status['spent'], currency)} of {_f(status['budget'], currency)} ({pct:.0f}%)."
            tail = f"{_f(status['remaining'], currency)} left. Watch the pace 👀"
    return f"{head}\n{zone_dot(pct)} {pct:.1f}%\n{body}\n\n{tail}"


def format_large_tx_alert(amount: float, category: str, avg: float, lang: str, currency: str = DEFAULT_CURRENCY) -> str:
    emoji = CATEGORY_EMOJI.get(category, "📦")
    times = (amount / avg) if avg > 0 else 0
    if lang == "ru":
        head = "👀 <b>Крупная трата</b>"
        body = f"{emoji} {escape(category)} — <b>{_f(amount, currency)}</b>"
        cmp = f"Это в {times:.1f}× больше твоего среднего чека ({_f(avg, currency)})." if times >= 2 else ""
        return f"{head}\n{body}\n{cmp}".rstrip()
    head = "👀 <b>Large purchase</b>"
    body = f"{emoji} {escape(category)} — <b>{_f(amount, currency)}</b>"
    cmp = f"That's {times:.1f}× your average ticket ({_f(avg, currency)})." if times >= 2 else ""
    return f"{head}\n{body}\n{cmp}".rstrip()


def format_daily_digest(total: float, n: int, top_cat: dict | None, status: dict, lang: str, currency: str = DEFAULT_CURRENCY) -> str:
    if lang == "ru":
        head = "🌙 <b>Итоги дня</b>"
        if n == 0:
            return f"{head}\n{DIVIDER}\n\n💚 Сегодня ни одной траты — отличный день для бюджета!"
        lines = [head, DIVIDER, "", f"Сегодня: <b>{_f(total, currency)}</b> · {_count_label(n, lang)}"]
        if top_cat:
            e = CATEGORY_EMOJI.get(top_cat["category"], "📦")
            lines.append(f"Больше всего: {e} {escape(top_cat['category'])} — {_f(top_cat['amount'], currency)}")
        lines.append("")
        lines.append(format_budget_status(status, lang, currency=currency))
        return "\n".join(lines)
    head = "🌙 <b>Today's wrap-up</b>"
    if n == 0:
        return f"{head}\n{DIVIDER}\n\n💚 No spending today — great day for your budget!"
    lines = [head, DIVIDER, "", f"Today: <b>{_f(total, currency)}</b> · {_count_label(n, lang)}"]
    if top_cat:
        e = CATEGORY_EMOJI.get(top_cat["category"], "📦")
        lines.append(f"Biggest: {e} {escape(top_cat['category'])} — {_f(top_cat['amount'], currency)}")
    lines.append("")
    lines.append(format_budget_status(status, lang, currency=currency))
    return "\n".join(lines)


def format_weekly_summary(total: float, n: int, top3: list, prev_total: float | None, lang: str, currency: str = DEFAULT_CURRENCY) -> str:
    head = "📅 <b>Итоги недели</b>" if lang == "ru" else "📅 <b>Weekly summary</b>"
    lines = [head, DIVIDER, ""]
    if n == 0:
        msg = "💚 За неделю ни одной траты!" if lang == "ru" else "💚 No spending this week!"
        lines.append(msg)
        return "\n".join(lines)
    if lang == "ru":
        lines.append(f"Потрачено за 7 дней: <b>{_f(total, currency)}</b> · {_count_label(n, lang)}")
    else:
        lines.append(f"Spent in 7 days: <b>{_f(total, currency)}</b> · {_count_label(n, lang)}")
    if prev_total is not None and prev_total > 0:
        delta = (total - prev_total) / prev_total * 100
        chip = delta_chip(delta, lang)
        vs = "к прошлой неделе" if lang == "ru" else "vs last week"
        lines.append(f"🔁 {vs}: {chip}")
    if top3:
        lines.append("")
        where = "Куда ушло:" if lang == "ru" else "Where it went:"
        lines.append(where)
        total_for_share = sum(c["amount"] for c in top3) or 1.0
        for c in top3:
            e = CATEGORY_EMOJI.get(c["category"], "📦")
            share = c["amount"] / total_for_share * 100
            lines.append(f"{e} {escape(c['category'])} — {_f(c['amount'], currency)} ({share:.0f}%)")
    return "\n".join(lines)


def _debt_due(due, today: date | None):
    """Return (days_left, overdue) for a debt due date, or (None, False)."""
    if not due:
        return None, False
    try:
        dl = date.fromisoformat(str(due)[:10])
    except (ValueError, TypeError):
        return None, False
    ref = today or date.today()
    days = (dl - ref).days
    return days, days < 0


def _debt_due_str(due, lang: str, today: date | None) -> str:
    """' · до 5 июл · ещё 3 дня' / ' · просрочен на 2 дня' / ''."""
    days, overdue = _debt_due(due, today)
    if days is None:
        return ""
    try:
        dl = date.fromisoformat(str(due)[:10])
        ds = _short_date(dl, lang)
    except (ValueError, TypeError):
        ds = str(due)
    if overdue:
        n = _days_label(abs(days), lang)
        return f" · ⏰ просрочен на {n}" if lang == "ru" else f" · ⏰ {n} overdue"
    until = "до" if lang == "ru" else "by"
    if days == 0:
        tail = "сегодня" if lang == "ru" else "today"
        return f" · {until} {ds} · {tail}"
    n = _days_label(days, lang)
    tail = f"ещё {n}" if lang == "ru" else f"{n} left"
    return f" · {until} {ds} · {tail}"


def format_debts_list(debts: list, lang: str, today: date | None = None) -> str:
    """Full debts overview for the bot: 'мне должны' and 'я должен', open only."""
    head = "🤝 <b>Долги</b>" if lang == "ru" else "🤝 <b>Debts</b>"
    open_debts = [d for d in debts if d.get("status") == "open"]
    if not open_debts:
        empty = ("📭 Открытых долгов нет.\nДобавь в приложении — напомню о сроке."
                 if lang == "ru" else
                 "📭 No open debts.\nAdd one in the app — I'll remind you before it's due.")
        return f"{head}\n{DIVIDER}\n\n{empty}"

    mine = [d for d in open_debts if d.get("direction") == "owed_to_me"]
    theirs = [d for d in open_debts if d.get("direction") == "i_owe"]

    def _line(d):
        cur = d.get("currency") or DEFAULT_CURRENCY
        who = escape(str(d.get("counterparty", "—")))
        return f"• <b>{who}</b> — {_f(d.get('amount', 0), cur)}{_debt_due_str(d.get('due_date'), lang, today)}"

    lines = [head, DIVIDER]
    if mine:
        total = {}
        for d in mine:
            cur = d.get("currency") or DEFAULT_CURRENCY
            total[cur] = total.get(cur, 0) + float(d.get("amount", 0) or 0)
        sub = " · ".join(_f(v, c) for c, v in total.items())
        lines.append("")
        lines.append(f"📥 <b>Мне должны</b> · {sub}" if lang == "ru" else f"📥 <b>Owed to me</b> · {sub}")
        lines += [_line(d) for d in mine]
    if theirs:
        total = {}
        for d in theirs:
            cur = d.get("currency") or DEFAULT_CURRENCY
            total[cur] = total.get(cur, 0) + float(d.get("amount", 0) or 0)
        sub = " · ".join(_f(v, c) for c, v in total.items())
        lines.append("")
        lines.append(f"📤 <b>Я должен</b> · {sub}" if lang == "ru" else f"📤 <b>I owe</b> · {sub}")
        lines += [_line(d) for d in theirs]
    return "\n".join(lines)


def format_debt_reminder(debts: list, lang: str, today: date | None = None) -> str:
    """Concise nudge about debts due within 3 days or overdue. '' if none."""
    ref = today or date.today()
    soon = []
    for d in debts:
        if d.get("status") != "open":
            continue
        days, overdue = _debt_due(d.get("due_date"), ref)
        if days is None:
            continue
        if overdue or days <= 3:
            soon.append((days, d))
    if not soon:
        return ""
    soon.sort(key=lambda x: x[0])
    head = "🤝 <b>Напоминание о долгах</b>" if lang == "ru" else "🤝 <b>Debt reminder</b>"
    lines = [head, DIVIDER, ""]
    for _days, d in soon:
        cur = d.get("currency") or DEFAULT_CURRENCY
        who = escape(str(d.get("counterparty", "—")))
        if d.get("direction") == "owed_to_me":
            verb = f"{who} должен тебе" if lang == "ru" else f"{who} owes you"
        else:
            verb = f"Ты должен {who}" if lang == "ru" else f"You owe {who}"
        lines.append(f"• {verb} <b>{_f(d.get('amount', 0), cur)}</b>{_debt_due_str(d.get('due_date'), lang, ref)}")
    return "\n".join(lines)


def format_goal_pulse(goals: list, lang: str, part: str = "morning", today: date | None = None) -> str:
    """Twice-daily (morning/evening) digest of all active goals with progress + pace.
    Returns '' if there are no active goals (caller should skip sending)."""
    active = [g for g in goals if g.get("status") == "active"]
    if not active:
        return ""
    if lang == "ru":
        head = "☀️ <b>Доброе утро!</b>" if part == "morning" else "🌙 <b>Добрый вечер!</b>"
        sub = "Не забывай про свои цели 🎯" if part == "morning" else "Как продвигаются твои цели 🎯"
    else:
        head = "☀️ <b>Good morning!</b>" if part == "morning" else "🌙 <b>Good evening!</b>"
        sub = "Don't forget your goals 🎯" if part == "morning" else "How your goals are going 🎯"
    lines = [f"{head} {sub}", DIVIDER, ""]
    for g in active:
        cur = g.get("currency") or DEFAULT_CURRENCY
        p = goal_progress(g, today)
        emoji = g.get("emoji") or "🎯"
        title = escape(str(g.get("title", "")))
        lines.append(f"{emoji} <b>{title}</b>  ·  {p['percent']:.0f}%")
        sub2 = f"{_f(p['saved'], cur)} / {_f(p['target'], cur)}"
        if p["days_left"] is not None and p["days_left"] >= 0:
            dleft = _days_label(p["days_left"], lang)
            if p["per_day"]:
                sub2 += (f" · ещё {dleft} · ≈ {_f(p['per_day'], cur)}/день"
                         if lang == "ru" else f" · {dleft} left · ≈ {_f(p['per_day'], cur)}/day")
            else:
                sub2 += (f" · ещё {dleft}" if lang == "ru" else f" · {dleft} left")
        elif p["overdue"]:
            sub2 += " · ⏰ срок прошёл" if lang == "ru" else " · ⏰ overdue"
        lines.append(f"<i>{sub2}</i>")
        lines.append("")
    return "\n".join(lines).rstrip()
