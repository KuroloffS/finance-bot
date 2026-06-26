"""FastAPI backend for the Telegram Mini App.

Serves the single-page web app and a small JSON API. Every /api route is
authenticated with Telegram initData (see web/auth.py); the user identity is
taken only from the verified payload.
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

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
    add_goal_contribution,
    create_goal,
    delete_all_transactions,
    delete_goal,
    delete_transaction,
    get_daily_spent_last_n,
    get_goals,
    get_month_spent_through_day,
    get_monthly_summary,
    get_monthly_summary_for,
    get_or_create_user,
    get_transactions_for_month,
    notify_settings_of,
    now_local,
    save_transaction,
    update_budget,
    update_currency,
    update_goal,
    update_language,
    update_notify_settings,
)
from utils.formatters import CATEGORY_EMOJI, compute_analytics, goal_progress
from web.auth import validate_init_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CATEGORIES = list(CATEGORY_EMOJI.keys())
DEFAULT_BUDGET = float(os.getenv("DEFAULT_MONTHLY_BUDGET", 5_000_000))


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
            return FileResponse(str(idx))
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
    async def api_overview(month: str | None = None, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        budget = _f(user.get("monthly_budget", DEFAULT_BUDGET))
        month_first, ref, is_current, y, m = _month_bounds(month)

        sparkline = None
        prev_through = None
        trend30 = None
        if is_current:
            pm = m - 1 or 12
            py = y if m > 1 else y - 1
            # Independent reads in parallel; the 7-day sparkline is the tail of the
            # 30-day trend, so we derive it instead of issuing a second daily query.
            summary, trend30, prev_through = await asyncio.gather(
                get_monthly_summary_for(uid, month_first),
                get_daily_spent_last_n(uid, 30),
                get_month_spent_through_day(uid, py, pm, ref.day),
            )
            sparkline = trend30[-7:]
        else:
            summary = await get_monthly_summary_for(uid, month_first)

        a = compute_analytics(
            summary, budget, ref,
            sparkline=sparkline, prev_through_day=prev_through, is_current=is_current,
        )

        categories = [
            {
                "category": r.get("category", "Другое"),
                "emoji": CATEGORY_EMOJI.get(r.get("category", "Другое"), "📦"),
                "total": _f(r.get("total_spent")),
                "count": int(r.get("num_transactions", 0)),
            }
            for r in sorted(summary, key=lambda x: _f(x.get("total_spent")), reverse=True)
        ]
        total = sum(c["total"] for c in categories) or 0.0
        for c in categories:
            c["share"] = (c["total"] / total * 100) if total else 0.0

        return {
            "month": f"{y:04d}-{m:02d}",
            "is_current": is_current,
            "currency": user.get("currency", "UZS"),
            "budget": budget,
            "spent": a["spent"],
            "remaining": a["remaining"],
            "percent": a["percent"],
            "warning": a["warning"],
            "categories": categories,
            "analytics": {
                "burn": a["burn"],
                "safe_daily": a["safe_daily"],
                "projection": a["projection"],
                "proj_delta": a["proj_delta"],
                "proj_pct": a["proj_pct"],
                "mom_delta_pct": a["mom_delta_pct"],
                "prev_total": a["prev_total"],
                "days_left": a["days_left"],
                "n_total": a["n_total"],
                "avg_ticket": a["avg_ticket"],
                "sparkline": a["sparkline"],
                "peak_weekday": a["peak_weekday"],
                "trend30": trend30,
            },
        }

    @app.get("/api/transactions")
    async def api_transactions(month: str | None = None, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        month_first, _ref, _ic, _y, _m = _month_bounds(month)
        rows = await get_transactions_for_month(uid, month_first)
        txs = [
            {
                "id": r.get("id"),
                "amount": _f(r.get("amount")),
                "category": r.get("category", "Другое"),
                "emoji": CATEGORY_EMOJI.get(r.get("category", "Другое"), "📦"),
                "description": r.get("description") or "",
                "merchant": r.get("merchant") or "",
                "input_type": r.get("input_type", "text"),
                "purchase_date": str(r.get("purchase_date", ""))[:10],
                "created_at": r.get("created_at"),
                "original_amount": _f(r.get("original_amount")) if r.get("original_amount") else None,
                "original_currency": r.get("original_currency"),
            }
            for r in rows
        ]
        return {"transactions": txs}

    class NewTx(BaseModel):
        amount: float
        category: str
        description: str | None = None
        merchant: str | None = None
        purchase_date: str | None = None
        currency: str | None = None

    @app.post("/api/transactions")
    async def api_add(tx: NewTx, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        if tx.amount is None or tx.amount <= 0:
            raise HTTPException(status_code=400, detail="amount must be > 0")
        category = tx.category if tx.category in CATEGORIES else "Другое"
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
        )
        return {"ok": True, "transaction": saved}

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
        if b.amount is None or b.amount <= 0:
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
        return {"categories": [{"name": c, "emoji": CATEGORY_EMOJI[c]} for c in CATEGORIES]}

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
        if g.target_amount is None or g.target_amount <= 0:
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
        if b.amount is None or b.amount == 0:
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

    return app
