"""FastAPI backend for the Telegram Mini App.

Serves the single-page web app and a small JSON API. Every /api route is
authenticated with Telegram initData (see web/auth.py); the user identity is
taken only from the verified payload.
"""
import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.groq_service import get_savings_tips, parse_text_purchase
from services.supabase_service import (
    delete_all_transactions,
    delete_transaction,
    get_daily_spent_last_n,
    get_month_spent_through_day,
    get_monthly_summary,
    get_monthly_summary_for,
    get_or_create_user,
    get_transactions_for_month,
    make_budget_status,
    now_local,
    save_transaction,
    update_budget,
    update_language,
)
from utils.formatters import CATEGORY_EMOJI, compute_analytics
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
            "currency": user.get("currency", "UZS"),
            "language": user.get("language", "ru"),
        }

    @app.get("/api/overview")
    async def api_overview(month: str | None = None, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        budget = _f(user.get("monthly_budget", DEFAULT_BUDGET))
        month_first, ref, is_current, y, m = _month_bounds(month)

        summary = await get_monthly_summary_for(uid, month_first)

        sparkline = None
        prev_through = None
        trend30 = None
        if is_current:
            sparkline = await get_daily_spent_last_n(uid, 7)
            trend30 = await get_daily_spent_last_n(uid, 30)
            pm = m - 1 or 12
            py = y if m > 1 else y - 1
            prev_through = await get_month_spent_through_day(uid, py, pm, ref.day)

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

    @app.post("/api/transactions")
    async def api_add(tx: NewTx, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        if tx.amount is None or tx.amount <= 0:
            raise HTTPException(status_code=400, detail="amount must be > 0")
        category = tx.category if tx.category in CATEGORIES else "Другое"
        saved = await save_transaction(
            uid, float(tx.amount), category,
            (tx.description or "").strip(), (tx.merchant or "").strip() or None,
            "", "app", purchase_date=tx.purchase_date,
        )
        return {"ok": True, "transaction": saved}

    class QuickAdd(BaseModel):
        text: str

    @app.post("/api/quick-add")
    async def api_quick_add(q: QuickAdd, user: dict = Depends(current_user)):
        uid = user["telegram_id"]
        budget = _f(user.get("monthly_budget", DEFAULT_BUDGET))
        ctx = {"language": user.get("language", "ru"), "monthly_budget": budget, "spent_this_month": 0}
        result = await parse_text_purchase(q.text or "", ctx)
        if not result.get("amount"):
            raise HTTPException(status_code=422, detail="Could not parse")
        saved = await save_transaction(
            uid, float(result["amount"]), result["category"],
            result.get("description", ""), result.get("merchant"),
            result.get("advice", ""), "app",
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
        summary = await get_monthly_summary(uid)
        if not summary:
            return {"tips": ""}
        tips = await get_savings_tips(summary, lang)
        return {"tips": tips or ""}

    @app.post("/api/reset")
    async def api_reset(user: dict = Depends(current_user)):
        n = await delete_all_transactions(user["telegram_id"])
        return {"ok": True, "deleted": n}

    @app.get("/api/categories")
    async def api_categories():
        return {"categories": [{"name": c, "emoji": CATEGORY_EMOJI[c]} for c in CATEGORIES]}

    return app
