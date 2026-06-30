"""FastAPI backend for the Telegram Mini App.

Serves the single-page web app and a small JSON API. Every /api route is
authenticated with Telegram initData (see web/auth.py); the user identity is
taken only from the verified payload.
"""
import asyncio
import logging
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.currency_service import (
    CURRENCIES,
    DEFAULT_CURRENCY,
    convert,
    normalize_currency,
)
from services.groq_service import get_savings_tips, parse_text_purchase
from services.supabase_service import (
    add_debt_payment,
    add_goal_contribution,
    create_debt,
    create_event,
    create_goal,
    create_payment,
    create_task,
    create_task_folder,
    delete_account,
    delete_all_transactions,
    delete_debt,
    delete_event,
    delete_goal,
    delete_payment,
    delete_task,
    delete_task_folder,
    delete_transaction,
    get_all_events,
    get_balance,
    get_debts,
    get_events_for_month,
    get_goals,
    get_monthly_summary,
    get_payments,
    get_period_report,
    get_or_create_user,
    get_tasks,
    get_task_folders,
    get_transactions_for_month,
    mark_payment_paid,
    notify_settings_of,
    now_local,
    save_transaction,
    settle_debt,
    update_budget,
    update_currency,
    update_event,
    update_goal,
    update_language,
    update_notify_settings,
    update_payment,
    update_task,
    wipe_user_data,
)
from utils.formatters import CATEGORY_EMOJI, goal_progress
from web.auth import validate_init_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CATEGORIES = list(CATEGORY_EMOJI.keys())
DEFAULT_BUDGET = float(os.getenv("DEFAULT_MONTHLY_BUDGET", 5_000_000))

# Income categories (transactions.type='income'). Expenses use CATEGORY_EMOJI.
INCOME_EMOJI = {
    "Зарплата": "💰",
    "Фриланс": "💻",
    "Бизнес": "🏢",
    "Подарок": "🎁",
    "Продажа": "🏷️",
    "Проценты": "📈",
    "Другое": "📦",
}
INCOME_CATEGORIES = list(INCOME_EMOJI.keys())

# Recurring-payment categories (Оплата).
PAYMENT_EMOJI = {
    "Подписка": "🔁",
    "Спортзал": "🏋️",
    "Аренда": "🏠",
    "Кредит": "💳",
    "Коммуналка": "💡",
    "Связь": "📱",
    "Страховка": "🛡️",
    "Другое": "📦",
}
PAYMENT_CATEGORIES = list(PAYMENT_EMOJI.keys())


# ───────────────────────── Auth dependency ─────────────────────────

async def current_user(
    x_telegram_init_data: str = Header(default=""),
) -> dict:
    user = validate_init_data(x_telegram_init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    # Ensure the user row exists (opening the app before /start).
    row = await get_or_create_user(
        int(user["id"]),
        user.get("username"),
        user.get("first_name"),
    )
    row["telegram_id"] = int(user["id"])
    row.setdefault("first_name", user.get("first_name"))
    return row


def _month_bounds(month: str | None):
    """Return (month_first 'YYYY-MM-01', ref_datetime, is_current)."""
    now = now_local()
    if month:
        try:
            y, m = month.split("-")
            y, m = int(y), int(m)
        except (ValueError, AttributeError):
            y, m = now.year, now.month
    else:
        y, m = now.year, now.month
    is_current = (y == now.year and m == now.month)
    ref = now if is_current else datetime(y, m, 1)
    return f"{y:04d}-{m:02d}-01", ref, is_current, y, m


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _valid_amount(v) -> bool:
    """Positive, finite, within a sane ceiling — rejects inf/NaN from crafted JSON."""
    try:
        return v is not None and math.isfinite(v) and 0 < v <= 1e15
    except (TypeError, ValueError):
        return False


def _goal_dto(g: dict, today) -> dict:
    """Serialize a goal row + computed progress for the Mini App."""
    p = goal_progress(g, today)
    return {
        "id": g.get("id"),
        "title": g.get("title", ""),
        "emoji": g.get("emoji") or "🎯",
        "target_amount": _f(g.get("target_amount")),
        "saved_amount": _f(g.get("saved_amount")),
        "currency": normalize_currency(g.get("currency"), DEFAULT_CURRENCY),
        "deadline": str(g.get("deadline"))[:10] if g.get("deadline") else None,
        "status": g.get("status", "active"),
        "percent": round(p["percent"], 1),
        "remaining": p["remaining"],
        "days_left": p["days_left"],
        "per_day": p["per_day"],
        "overdue": p["overdue"],
        "done": p["done"],
    }


def _tx_dto(r: dict) -> dict:
    """Serialize a transaction row (income or expense) for the Mini App."""
    cat = r.get("category", "Другое")
    ttype = r.get("type", "expense") or "expense"
    if ttype == "income":
        emoji = INCOME_EMOJI.get(cat) or "💰"
    else:
        emoji = CATEGORY_EMOJI.get(cat) or "📦"
    return {
        "id": r.get("id"),
        "type": ttype,
        "amount": _f(r.get("amount")),
        "category": cat,
        "emoji": emoji,
        "description": r.get("description") or "",
        "merchant": r.get("merchant") or "",
        "input_type": r.get("input_type", "text"),
        "purchase_date": str(r.get("purchase_date", ""))[:10],
        "created_at": r.get("created_at"),
        "original_amount": _f(r.get("original_amount")) if r.get("original_amount") else None,
        "original_currency": r.get("original_currency"),
    }


def _payment_dto(p: dict, today) -> dict:
    """Serialize a recurring payment + paid/overdue status for the Оплата screen."""
    next_due = None
    days_left = None
    overdue = False
    due_today = False
    nd = p.get("next_due_date")
    if nd:
        try:
            ndd = date.fromisoformat(str(nd)[:10])
            next_due = ndd.isoformat()
            days_left = (ndd - today).days
            overdue = days_left < 0
            due_today = days_left == 0
        except (ValueError, TypeError):
            pass
    last_paid = str(p.get("last_paid_date"))[:10] if p.get("last_paid_date") else None
    status = p.get("status", "active")
    # Paid for the current cycle = has a recorded payment and the next charge is still ahead.
    paid = bool(last_paid) and days_left is not None and days_left > 0
    return {
        "id": p.get("id"),
        "name": p.get("name", ""),
        "category": p.get("category", "Подписка"),
        "emoji": PAYMENT_EMOJI.get(p.get("category", "Подписка"), "🔁"),
        "amount": _f(p.get("amount")),
        "currency": normalize_currency(p.get("currency"), DEFAULT_CURRENCY),
        "period": p.get("period", "monthly"),
        "next_due_date": next_due,
        "last_paid_date": last_paid,
        "status": status,
        "note": p.get("note") or "",
        "days_left": days_left,
        "overdue": overdue and status == "active",
        "due_today": due_today,
        "paid": paid,
    }


def _event_dto(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "title": e.get("title", ""),
        "date": str(e.get("event_date", ""))[:10],
        "time": str(e.get("event_time"))[:5] if e.get("event_time") else None,
        "note": e.get("note") or "",
        "emoji": e.get("emoji") or "📌",
    }


def _task_dto(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "title": t.get("title", ""),
        "status": t.get("status", "active"),
        "priority": t.get("priority"),
        "due_date": str(t.get("due_date"))[:10] if t.get("due_date") else None,
        "folder_id": t.get("folder_id"),
        "tags": t.get("tags") or [],
        "note": t.get("note") or "",
        "created_at": t.get("created_at"),
        "completed_at": t.get("completed_at"),
    }


# ───────────────────────── Data export (Excel) + bot delivery ─────────────────────────

async def _gather_export(uid: int) -> dict:
    """Collect all of a user's data for export (transactions, goals, debts,
    events, payments, tasks, folders)."""
    today = now_local().date()
    rep, goals, debts, events, payments, tasks, folders = await asyncio.gather(
        get_period_report(uid, "1970-01-01", today.isoformat()),
        get_goals(uid, include_archived=True),
        get_debts(uid),
        get_all_events(uid),
        get_payments(uid),
        get_tasks(uid),
        get_task_folders(uid),
    )
    return {
        "exported_at": now_local().isoformat(),
        "transactions": [_tx_dto(r) for r in rep["transactions"]],
        "goals": goals,
        "debts": debts,
        "events": [_event_dto(e) for e in events],
        "payments": [_payment_dto(p, today) for p in payments],
        "tasks": [_task_dto(t) for t in tasks],
        "folders": folders,
    }


_NUMRE = re.compile(r"-?\d+(\.\d+)?")


def _cell(v):
    """Coerce a value for an Excel cell — numeric strings become real numbers."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = str(v)
    if _NUMRE.fullmatch(s):
        try:
            return float(s)
        except ValueError:
            pass
    return s


def _build_export_xls(data: dict) -> bytes:
    """Real .xlsx workbook (OOXML) via openpyxl — one sheet per module."""
    from io import BytesIO
    from openpyxl import Workbook

    sheets = [
        ("Операции", ["Дата", "Тип", "Категория", "Сумма", "Заметка", "Магазин"],
         [[t["purchase_date"], "доход" if t["type"] == "income" else "расход", t["category"],
           t["amount"], t["description"], t["merchant"]] for t in data.get("transactions", [])]),
        ("Платежи", ["Название", "Категория", "Сумма", "Валюта", "Период", "След. платёж", "Статус"],
         [[p["name"], p["category"], p["amount"], p["currency"], p["period"], p["next_due_date"], p["status"]]
          for p in data.get("payments", [])]),
        ("Задачи", ["Задача", "Статус", "Приоритет", "Срок", "Теги"],
         [[t["title"], t["status"], t.get("priority") or "", t.get("due_date") or "",
           ", ".join(t.get("tags") or [])] for t in data.get("tasks", [])]),
        ("События", ["Дата", "Время", "Событие", "Заметка"],
         [[e["date"], e.get("time") or "", e["title"], e["note"]] for e in data.get("events", [])]),
        ("Цели", ["Цель", "Накоплено", "Сумма цели", "Валюта", "Статус"],
         [[x.get("title", ""), x.get("saved_amount", 0), x.get("target_amount", 0),
           x.get("currency", ""), x.get("status", "")] for x in data.get("goals", [])]),
        ("Долги", ["Контрагент", "Направление", "Сумма", "Оплачено", "Валюта", "Статус"],
         [[x.get("counterparty", ""), x.get("direction", ""), x.get("amount", 0),
           x.get("paid_amount", 0), x.get("currency", ""), x.get("status", "")] for x in data.get("debts", [])]),
    ]
    wb = Workbook()
    for i, (name, headers, rows) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name[:31]
        ws.append(headers)
        for r in rows:
            ws.append([_cell(c) for c in r])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _send_tg_document(chat_id: int, filename: str, content: bytes, caption: str = "") -> bool:
    """Send a file to the user's Telegram chat via the Bot API (reliable on mobile,
    unlike a webview blob download)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    files = {"document": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendDocument", data=data, files=files)
        return r.status_code == 200 and bool(r.json().get("ok"))
    except Exception as e:
        logger.warning("sendDocument failed for %s: %s", chat_id, e)
        return False


# ───────────────────────── App ─────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(title="Finance Mini App", docs_url=None, redoc_url=None)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/")
    async def index():
        idx = STATIC_DIR / "index.html"
        if idx.exists():
            # Always revalidate so a new deploy's Mini App HTML is picked up.
            return FileResponse(str(idx), headers={"Cache-Control": "no-cache, must-revalidate"})
        return JSONResponse({"error": "app not built"}, status_code=503)

    @app.get("/api/me")
    async def api_me(user: dict = Depends(current_user)):
        return {
            "first_name": user.get("first_name"),
            "budget": _f(user.get("monthly_budget", DEFAULT_BUDGET)),
            "currency": normalize_currency(user.get("currency"), DEFAULT_CURRENCY),
            "language": user.get("language", "ru"),
            "notify": notify_settings_of(user),
            "currencies": [
                {"code": c, "flag": m["flag"], "symbol": m["symbol"],
                 "name": m["name_en"], "name_ru": m["name_ru"]}
                for c, m in CURRENCIES.items()
            ],
        }

    @app.get("/api/overview")
    async def api_overview(user: dict = Depends(current_user)):
        """Dayon home: running balance, this-month income/expense/avg-per-day,
        recent operations, and upcoming obligations (active recurring payments)."""
        uid = user["telegram_id"]
        base = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        budget = _f(user.get("monthly_budget", DEFAULT_BUDGET))
        now = now_local()
        today = now.date()
        month_first = f"{now.year:04d}-{now.month:02d}-01"

        balance, report, payments = await asyncio.gather(
            get_balance(uid),
            get_period_report(uid, month_first, today.isoformat()),
            get_payments(uid),
        )

        month_expense = report["expense"]
        month_income = report["income"]
        days_elapsed = today.day or 1
        avg_day = month_expense / days_elapsed
        percent = (month_expense / budget * 100) if budget > 0 else 0.0

        recent = [_tx_dto(r) for r in report["transactions"][:6]]
        obligations = [
            _payment_dto(p, today) for p in payments if p.get("status") == "active"
        ][:4]

        return {
            "currency": base,
            "balance": balance["balance"],
            "income_all": balance["income"],
            "expense_all": balance["expense"],
            "month_income": month_income,
            "month_expense": month_expense,
            "avg_day": avg_day,
            "month_count": report["count"],
            "budget": budget,
            "percent": percent,
            "warning": percent >= 100,
            "recent": recent,
            "obligations": obligations,
        }

    @app.get("/api/transactions")
    async def api_transactions(month: str | None = None, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        month_first, _ref, _ic, _y, _m = _month_bounds(month)
        rows = await get_transactions_for_month(uid, month_first)
        return {"transactions": [_tx_dto(r) for r in rows]}

    class NewTx(BaseModel):
        amount: float
        category: str
        type: str | None = None  # 'expense' (default) | 'income'
        description: str | None = None
        merchant: str | None = None
        purchase_date: str | None = None
        currency: str | None = None

    @app.post("/api/transactions")
    async def api_add(tx: NewTx, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        if not _valid_amount(tx.amount):
            raise HTTPException(status_code=400, detail="amount must be > 0")
        tx_type = "income" if tx.type == "income" else "expense"
        valid = INCOME_CATEGORIES if tx_type == "income" else CATEGORIES
        category = tx.category if tx.category in valid else "Другое"
        base = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        entry_cur = normalize_currency(tx.currency, base)
        amount = float(tx.amount)
        original_amount = original_currency = None
        base_amount = amount
        if entry_cur != base:
            base_amount = await convert(amount, entry_cur, base)
            original_amount, original_currency = amount, entry_cur
        saved = await save_transaction(
            uid, base_amount, category,
            (tx.description or "").strip(), (tx.merchant or "").strip() or None,
            "", "app", purchase_date=tx.purchase_date,
            original_amount=original_amount, original_currency=original_currency,
            tx_type=tx_type,
        )
        return {"ok": True, "transaction": _tx_dto(saved) if saved else None}

    class QuickAdd(BaseModel):
        text: str

    @app.post("/api/quick-add")
    async def api_quick_add(q: QuickAdd, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        budget = _f(user.get("monthly_budget", DEFAULT_BUDGET))
        base = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        ctx = {
            "language": user.get("language", "ru"),
            "monthly_budget": budget,
            "spent_this_month": 0,
            "currency": base,
        }
        result = await parse_text_purchase(q.text or "", ctx)
        if not result.get("amount"):
            raise HTTPException(status_code=422, detail="Could not parse")
        entry_cur = normalize_currency(result.get("currency"), base)
        amount = float(result["amount"])
        original_amount = original_currency = None
        base_amount = amount
        if entry_cur != base:
            base_amount = await convert(amount, entry_cur, base)
            original_amount, original_currency = amount, entry_cur
        result["amount"] = base_amount
        saved = await save_transaction(
            uid, base_amount, result["category"],
            result.get("description", ""), result.get("merchant"),
            result.get("advice", ""), "app",
            original_amount=original_amount, original_currency=original_currency,
        )
        return {"ok": True, "transaction": saved, "parsed": result}

    @app.delete("/api/transactions/{tx_id}")
    async def api_delete(tx_id: int, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        deleted = await delete_transaction(uid, tx_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Not found")
        return {"ok": True}

    class BudgetBody(BaseModel):
        amount: float

    @app.post("/api/budget")
    async def api_budget(b: BudgetBody, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        if not _valid_amount(b.amount):
            raise HTTPException(status_code=400, detail="amount must be > 0")
        await update_budget(uid, float(b.amount))
        return {"ok": True, "budget": float(b.amount)}

    class LangBody(BaseModel):
        language: str

    @app.post("/api/language")
    async def api_language(b: LangBody, user: dict = Depends(current_user)):
        lang = b.language if b.language in ("ru", "en") else "ru"
        await update_language(user["telegram_id"], lang)
        return {"ok": True, "language": lang}

    @app.get("/api/tips")
    async def api_tips(user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        lang = user.get("language", "ru")
        cur = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        summary = await get_monthly_summary(uid)
        if not summary:
            return {"tips": ""}
        tips = await get_savings_tips(summary, lang, cur)
        return {"tips": tips or ""}

    @app.post("/api/reset")
    async def api_reset(user: dict = Depends(current_user)):
        n = await delete_all_transactions(user["telegram_id"])
        return {"ok": True, "deleted": n}

    @app.get("/api/categories")
    async def api_categories():
        return {
            "categories": [{"name": c, "emoji": CATEGORY_EMOJI[c]} for c in CATEGORIES],
            "income": [{"name": c, "emoji": INCOME_EMOJI[c]} for c in INCOME_CATEGORIES],
            "payment": [{"name": c, "emoji": PAYMENT_EMOJI[c]} for c in PAYMENT_CATEGORIES],
        }

    # ───────────────────────── Currency ─────────────────────────

    class CurrencyBody(BaseModel):
        currency: str

    @app.post("/api/currency")
    async def api_currency(b: CurrencyBody, user: dict = Depends(current_user)):
        code = normalize_currency(b.currency, "")
        if code not in CURRENCIES:
            raise HTTPException(status_code=400, detail="unsupported currency")
        await update_currency(user["telegram_id"], code)
        return {"ok": True, "currency": code}

    # ───────────────────────── Notification settings ─────────────────────────

    class NotifyBody(BaseModel):
        settings: dict

    @app.post("/api/settings")
    async def api_settings(b: NotifyBody, user: dict = Depends(current_user)):
        merged = notify_settings_of(user)
        for k, v in (b.settings or {}).items():
            if k in merged:
                merged[k] = bool(v)
        saved = await update_notify_settings(user["telegram_id"], merged)
        return {"ok": True, "notify": saved}

    # ───────────────────────── Savings goals ─────────────────────────

    @app.get("/api/goals")
    async def api_goals(user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        today = now_local().date()
        goals = await get_goals(uid)
        return {"goals": [_goal_dto(g, today) for g in goals]}

    class NewGoal(BaseModel):
        title: str
        target_amount: float
        currency: str | None = None
        emoji: str | None = None
        deadline: str | None = None
        saved_amount: float | None = None

    @app.post("/api/goals")
    async def api_create_goal(g: NewGoal, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        if not (g.title or "").strip():
            raise HTTPException(status_code=400, detail="title required")
        if not _valid_amount(g.target_amount):
            raise HTTPException(status_code=400, detail="target_amount must be > 0")
        cur = normalize_currency(g.currency, normalize_currency(user.get("currency"), DEFAULT_CURRENCY))
        goal = await create_goal(
            uid, g.title.strip(), float(g.target_amount), cur,
            emoji=(g.emoji or "🎯"), deadline=g.deadline,
            saved_amount=float(g.saved_amount or 0),
        )
        if not goal:
            raise HTTPException(status_code=500, detail="could not create goal")
        return {"ok": True, "goal": _goal_dto(goal, now_local().date())}

    class ContributeBody(BaseModel):
        amount: float
        note: str | None = None

    @app.post("/api/goals/{goal_id}/contribute")
    async def api_contribute(goal_id: int, b: ContributeBody, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        if b.amount is None or not math.isfinite(b.amount) or b.amount == 0 or abs(b.amount) > 1e15:
            raise HTTPException(status_code=400, detail="amount required")
        goal = await add_goal_contribution(uid, goal_id, float(b.amount), (b.note or "").strip() or None)
        if not goal:
            raise HTTPException(status_code=404, detail="goal not found")
        return {"ok": True, "goal": _goal_dto(goal, now_local().date())}

    class UpdateGoal(BaseModel):
        title: str | None = None
        emoji: str | None = None
        target_amount: float | None = None
        deadline: str | None = None
        status: str | None = None

    @app.put("/api/goals/{goal_id}")
    async def api_update_goal(goal_id: int, b: UpdateGoal, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        fields = {k: v for k, v in b.model_dump().items() if v is not None}
        if "deadline" in (b.model_fields_set or set()):
            fields["deadline"] = b.deadline  # allow explicit null to clear
        goal = await update_goal(uid, goal_id, fields)
        if not goal:
            raise HTTPException(status_code=404, detail="goal not found")
        return {"ok": True, "goal": _goal_dto(goal, now_local().date())}

    @app.delete("/api/goals/{goal_id}")
    async def api_delete_goal(goal_id: int, user: dict = Depends(current_user)):
        ok = await delete_goal(user["telegram_id"], goal_id)
        if not ok:
            raise HTTPException(status_code=404, detail="goal not found")
        return {"ok": True}

    # ───────────────────────── Debts / loans ─────────────────────────

    def _debt_dto(d: dict, today) -> dict:
        due = d.get("due_date")
        days_left = None
        overdue = False
        if due:
            try:
                dl = date.fromisoformat(str(due)[:10])
                days_left = (dl - today).days
                overdue = days_left < 0
            except (ValueError, TypeError):
                pass
        amount = _f(d.get("amount"))
        paid = _f(d.get("paid_amount"))
        remaining = max(0.0, amount - paid)
        return {
            "id": d.get("id"),
            "direction": d.get("direction", "owed_to_me"),
            "counterparty": d.get("counterparty", ""),
            "amount": amount,
            "paid_amount": paid,
            "remaining": remaining,
            "percent": round(paid / amount * 100, 1) if amount > 0 else 0.0,
            "currency": normalize_currency(d.get("currency"), DEFAULT_CURRENCY),
            "due_date": str(due)[:10] if due else None,
            "status": d.get("status", "open"),
            "note": d.get("note") or "",
            "days_left": days_left,
            "overdue": overdue,
        }

    @app.get("/api/debts")
    async def api_debts(user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        today = now_local().date()
        base = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        debts = await get_debts(uid)
        owed = owe = 0.0
        for d in debts:
            if d.get("status") != "open":
                continue
            remaining = max(0.0, float(d.get("amount") or 0) - float(d.get("paid_amount") or 0))
            amt = await convert(remaining, normalize_currency(d.get("currency"), base), base)
            if d.get("direction") == "i_owe":
                owe += amt
            else:
                owed += amt
        return {
            "debts": [_debt_dto(d, today) for d in debts],
            "summary": {"owed_to_me": owed, "i_owe": owe, "net": owed - owe, "currency": base},
        }

    class NewDebt(BaseModel):
        direction: str
        counterparty: str
        amount: float
        currency: str | None = None
        due_date: str | None = None
        note: str | None = None

    @app.post("/api/debts")
    async def api_create_debt(b: NewDebt, user: dict = Depends(current_user)):
        if not _valid_amount(b.amount):
            raise HTTPException(status_code=400, detail="amount must be > 0")
        if not (b.counterparty or "").strip():
            raise HTTPException(status_code=400, detail="counterparty required")
        cur = normalize_currency(b.currency, normalize_currency(user.get("currency"), DEFAULT_CURRENCY))
        direction = b.direction if b.direction in ("owed_to_me", "i_owe") else "owed_to_me"
        d = await create_debt(
            user["telegram_id"], direction, b.counterparty, float(b.amount), cur,
            due_date=b.due_date, note=b.note,
        )
        if not d:
            raise HTTPException(status_code=500, detail="could not create debt")
        return {"ok": True, "debt": _debt_dto(d, now_local().date())}

    class DebtPay(BaseModel):
        amount: float

    @app.post("/api/debts/{debt_id}/pay")
    async def api_pay_debt(debt_id: int, b: DebtPay, user: dict = Depends(current_user)):
        if not _valid_amount(b.amount):
            raise HTTPException(status_code=400, detail="amount must be > 0")
        d = await add_debt_payment(user["telegram_id"], debt_id, float(b.amount))
        if not d:
            raise HTTPException(status_code=404, detail="debt not found")
        return {"ok": True, "debt": _debt_dto(d, now_local().date())}

    @app.post("/api/debts/{debt_id}/settle")
    async def api_settle_debt(debt_id: int, user: dict = Depends(current_user)):
        d = await settle_debt(user["telegram_id"], debt_id)
        if not d:
            raise HTTPException(status_code=404, detail="debt not found")
        return {"ok": True, "debt": _debt_dto(d, now_local().date())}

    @app.delete("/api/debts/{debt_id}")
    async def api_delete_debt(debt_id: int, user: dict = Depends(current_user)):
        ok = await delete_debt(user["telegram_id"], debt_id)
        if not ok:
            raise HTTPException(status_code=404, detail="debt not found")
        return {"ok": True}

    # ───────────────────────── Reports (Отчёты) ─────────────────────────

    @app.get("/api/reports")
    async def api_reports(
        period: str = "month",
        date_from: str | None = None,
        date_to: str | None = None,
        user: dict = Depends(current_user),
    ):
        uid = user["telegram_id"]
        base = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        today = now_local().date()

        if period == "today":
            start = end = today
        elif period == "week":
            start, end = today - timedelta(days=6), today
        elif period == "year":
            start, end = date(today.year, 1, 1), today
        elif period == "custom":
            try:
                start = date.fromisoformat(str(date_from)[:10])
                end = date.fromisoformat(str(date_to)[:10])
            except (ValueError, TypeError):
                start, end = date(today.year, today.month, 1), today
            if end < start:
                start, end = end, start
        else:  # month (default)
            period = "month"
            start, end = date(today.year, today.month, 1), today

        rep = await get_period_report(uid, start.isoformat(), end.isoformat())

        cats: dict[str, float] = {}
        for r in rep["transactions"]:
            if r.get("type") == "income":
                continue
            c = r.get("category", "Другое")
            cats[c] = cats.get(c, 0.0) + _f(r.get("amount"))
        total = sum(cats.values()) or 0.0
        categories = [
            {
                "category": c,
                "emoji": CATEGORY_EMOJI.get(c, "📦"),
                "total": v,
                "share": (v / total * 100) if total else 0.0,
            }
            for c, v in sorted(cats.items(), key=lambda x: x[1], reverse=True)
        ]

        return {
            "period": period,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "currency": base,
            "income": rep["income"],
            "expense": rep["expense"],
            "balance": rep["balance"],
            "count": rep["count"],
            "categories": categories,
            "transactions": [_tx_dto(r) for r in rep["transactions"]],
        }

    # ───────────────────────── Calendar events (События) ─────────────────────────

    @app.get("/api/events")
    async def api_events(month: str | None = None, user: dict = Depends(current_user)):
        month_first, _ref, _ic, _y, _m = _month_bounds(month)
        rows = await get_events_for_month(user["telegram_id"], month_first)
        return {"events": [_event_dto(e) for e in rows]}

    class NewEvent(BaseModel):
        title: str
        date: str
        time: str | None = None
        note: str | None = None
        emoji: str | None = None

    @app.post("/api/events")
    async def api_create_event(b: NewEvent, user: dict = Depends(current_user)):
        if not (b.title or "").strip():
            raise HTTPException(status_code=400, detail="title required")
        e = await create_event(user["telegram_id"], b.title, b.date, b.time, b.note, b.emoji or "📌")
        if not e:
            raise HTTPException(status_code=500, detail="could not create event")
        return {"ok": True, "event": _event_dto(e)}

    class UpdateEvent(BaseModel):
        title: str | None = None
        date: str | None = None
        time: str | None = None
        note: str | None = None
        emoji: str | None = None

    @app.put("/api/events/{event_id}")
    async def api_update_event(event_id: int, b: UpdateEvent, user: dict = Depends(current_user)):
        s = b.model_fields_set
        fields: dict = {}
        if b.title:
            fields["title"] = b.title
        if b.date:
            fields["event_date"] = b.date
        if "time" in s:
            fields["event_time"] = b.time
        if "note" in s:
            fields["note"] = b.note
        if b.emoji:
            fields["emoji"] = b.emoji
        e = await update_event(user["telegram_id"], event_id, fields)
        if not e:
            raise HTTPException(status_code=404, detail="event not found")
        return {"ok": True, "event": _event_dto(e)}

    @app.delete("/api/events/{event_id}")
    async def api_delete_event(event_id: int, user: dict = Depends(current_user)):
        ok = await delete_event(user["telegram_id"], event_id)
        if not ok:
            raise HTTPException(status_code=404, detail="event not found")
        return {"ok": True}

    # ───────────────────────── Recurring payments (Оплата) ─────────────────────────

    @app.get("/api/payments")
    async def api_payments(user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        today = now_local().date()
        base = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        payments = await get_payments(uid)
        mult = {"weekly": 52 / 12, "monthly": 1.0, "yearly": 1 / 12}
        monthly_total = 0.0
        active = 0
        next_due = None
        for p in payments:
            if p.get("status") != "active":
                continue
            active += 1
            amt = await convert(_f(p.get("amount")), normalize_currency(p.get("currency"), base), base)
            monthly_total += amt * mult.get(p.get("period", "monthly"), 1.0)
            nd = str(p.get("next_due_date"))[:10] if p.get("next_due_date") else None
            if nd and (next_due is None or nd < next_due):
                next_due = nd
        return {
            "payments": [_payment_dto(p, today) for p in payments],
            "summary": {"count": active, "monthly_total": monthly_total, "next_due": next_due, "currency": base},
        }

    class NewPayment(BaseModel):
        name: str
        category: str | None = None
        amount: float
        currency: str | None = None
        period: str | None = None
        next_due_date: str
        note: str | None = None

    @app.post("/api/payments")
    async def api_create_payment(b: NewPayment, user: dict = Depends(current_user)):
        if not _valid_amount(b.amount):
            raise HTTPException(status_code=400, detail="amount must be > 0")
        if not (b.name or "").strip():
            raise HTTPException(status_code=400, detail="name required")
        cur = normalize_currency(b.currency, normalize_currency(user.get("currency"), DEFAULT_CURRENCY))
        category = b.category if b.category in PAYMENT_CATEGORIES else "Подписка"
        p = await create_payment(
            user["telegram_id"], b.name, category, float(b.amount), cur,
            period=(b.period or "monthly"), next_due_date=b.next_due_date, note=b.note,
        )
        if not p:
            raise HTTPException(status_code=500, detail="could not create payment")
        return {"ok": True, "payment": _payment_dto(p, now_local().date())}

    class UpdatePayment(BaseModel):
        name: str | None = None
        category: str | None = None
        amount: float | None = None
        currency: str | None = None
        period: str | None = None
        next_due_date: str | None = None
        note: str | None = None
        status: str | None = None

    @app.put("/api/payments/{payment_id}")
    async def api_update_payment(payment_id: int, b: UpdatePayment, user: dict = Depends(current_user)):
        s = b.model_fields_set
        fields: dict = {}
        for f in ("name", "category", "amount", "currency", "period", "next_due_date", "status"):
            v = getattr(b, f)
            if f in s and v:
                fields[f] = v
        if "note" in s:
            fields["note"] = b.note
        p = await update_payment(user["telegram_id"], payment_id, fields)
        if not p:
            raise HTTPException(status_code=404, detail="payment not found")
        return {"ok": True, "payment": _payment_dto(p, now_local().date())}

    @app.post("/api/payments/{payment_id}/pay")
    async def api_pay_payment(payment_id: int, user: dict = Depends(current_user)):
        p = await mark_payment_paid(user["telegram_id"], payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="payment not found")
        return {"ok": True, "payment": _payment_dto(p, now_local().date())}

    @app.post("/api/payments/{payment_id}/pause")
    async def api_pause_payment(payment_id: int, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        payments = await get_payments(uid)
        cur = next((p for p in payments if str(p.get("id")) == str(payment_id)), None)
        if not cur:
            raise HTTPException(status_code=404, detail="payment not found")
        new_status = "paused" if cur.get("status") == "active" else "active"
        p = await update_payment(uid, payment_id, {"status": new_status})
        if not p:
            raise HTTPException(status_code=404, detail="payment not found")
        return {"ok": True, "payment": _payment_dto(p, now_local().date())}

    @app.delete("/api/payments/{payment_id}")
    async def api_delete_payment(payment_id: int, user: dict = Depends(current_user)):
        ok = await delete_payment(user["telegram_id"], payment_id)
        if not ok:
            raise HTTPException(status_code=404, detail="payment not found")
        return {"ok": True}

    # ───────────────────────── Tasks (Задачи) ─────────────────────────

    @app.get("/api/tasks")
    async def api_tasks(user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        tasks, folders = await asyncio.gather(get_tasks(uid), get_task_folders(uid))
        counts = {"active": 0, "done": 0, "cancelled": 0}
        for t in tasks:
            st = t.get("status", "active")
            counts[st] = counts.get(st, 0) + 1
        return {
            "tasks": [_task_dto(t) for t in tasks],
            "folders": [{"id": f.get("id"), "name": f.get("name", ""), "emoji": f.get("emoji") or "📁"} for f in folders],
            "counts": counts,
        }

    class NewTask(BaseModel):
        title: str
        priority: str | None = None
        due_date: str | None = None
        folder_id: int | None = None
        tags: list[str] | None = None
        note: str | None = None

    @app.post("/api/tasks")
    async def api_create_task(b: NewTask, user: dict = Depends(current_user)):
        if not (b.title or "").strip():
            raise HTTPException(status_code=400, detail="title required")
        t = await create_task(
            user["telegram_id"], b.title, priority=b.priority, due_date=b.due_date,
            folder_id=b.folder_id, tags=b.tags, note=b.note,
        )
        if not t:
            raise HTTPException(status_code=500, detail="could not create task")
        return {"ok": True, "task": _task_dto(t)}

    class UpdateTask(BaseModel):
        title: str | None = None
        status: str | None = None
        priority: str | None = None
        due_date: str | None = None
        folder_id: int | None = None
        tags: list[str] | None = None
        note: str | None = None

    @app.put("/api/tasks/{task_id}")
    async def api_update_task(task_id: int, b: UpdateTask, user: dict = Depends(current_user)):
        s = b.model_fields_set
        fields: dict = {}
        if b.title:
            fields["title"] = b.title
        if "status" in s and b.status in ("active", "done", "cancelled"):
            fields["status"] = b.status
        for f in ("priority", "due_date", "folder_id", "note"):
            if f in s:
                fields[f] = getattr(b, f)  # allow explicit null to clear
        if "tags" in s:
            fields["tags"] = b.tags or []
        t = await update_task(user["telegram_id"], task_id, fields)
        if not t:
            raise HTTPException(status_code=404, detail="task not found")
        return {"ok": True, "task": _task_dto(t)}

    @app.delete("/api/tasks/{task_id}")
    async def api_delete_task(task_id: int, user: dict = Depends(current_user)):
        ok = await delete_task(user["telegram_id"], task_id)
        if not ok:
            raise HTTPException(status_code=404, detail="task not found")
        return {"ok": True}

    class NewFolder(BaseModel):
        name: str
        emoji: str | None = None

    @app.post("/api/task-folders")
    async def api_create_folder(b: NewFolder, user: dict = Depends(current_user)):
        if not (b.name or "").strip():
            raise HTTPException(status_code=400, detail="name required")
        f = await create_task_folder(user["telegram_id"], b.name, b.emoji or "📁")
        if not f:
            raise HTTPException(status_code=500, detail="could not create folder")
        return {"ok": True, "folder": {"id": f.get("id"), "name": f.get("name", ""), "emoji": f.get("emoji") or "📁"}}

    @app.delete("/api/task-folders/{folder_id}")
    async def api_delete_folder(folder_id: int, user: dict = Depends(current_user)):
        ok = await delete_task_folder(user["telegram_id"], folder_id)
        if not ok:
            raise HTTPException(status_code=404, detail="folder not found")
        return {"ok": True}

    # ───────────────────────── Data: export / wipe / delete account ─────────────────────────

    @app.get("/api/export")
    async def api_export(user: dict = Depends(current_user)):
        data = await _gather_export(user["telegram_id"])
        data["currency"] = normalize_currency(user.get("currency"), DEFAULT_CURRENCY)
        return data

    @app.post("/api/export/send")
    async def api_export_send(user: dict = Depends(current_user)):
        """Build the Excel workbook server-side and deliver it to the user's chat
        via the bot — works on mobile where webview file downloads don't."""
        uid = user["telegram_id"]
        data = await _gather_export(uid)
        xls = _build_export_xls(data)
        lang = user.get("language", "ru")
        caption = "📊 Ваши данные Dayon" if lang == "ru" else "📊 Your Dayon data"
        ok = await _send_tg_document(uid, "finances.xlsx", xls, caption)
        if not ok:
            raise HTTPException(status_code=502, detail="could not send file")
        return {"ok": True}

    @app.post("/api/wipe")
    async def api_wipe(user: dict = Depends(current_user)):
        counts = await wipe_user_data(user["telegram_id"])
        return {"ok": True, "deleted": counts}

    @app.delete("/api/account")
    async def api_delete_account(user: dict = Depends(current_user)):
        ok = await delete_account(user["telegram_id"])
        return {"ok": ok}

    return app
