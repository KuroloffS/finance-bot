import asyncio
import logging
import os
from datetime import date, datetime, timedelta

import pytz
from supabase import create_client, Client

from services.currency_service import convert, normalize_currency

logger = logging.getLogger(__name__)

_TZ = pytz.timezone("Asia/Tashkent")


def now_local() -> datetime:
    """Current time in the app timezone (Asia/Tashkent, UTC+5).
    Used everywhere a date/month/day is derived so transaction dates and
    month boundaries match the scheduler's timezone regardless of server tz."""
    return datetime.now(_TZ)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

_client: Client = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


async def get_or_create_user(
    telegram_id: int, username: str | None, first_name: str | None
) -> dict:
    try:
        def _get():
            return (
                get_client()
                .table("users")
                .select("*")
                .eq("telegram_id", telegram_id)
                .execute()
            )

        result = await asyncio.to_thread(_get)
        if result and result.data:
            return result.data[0]

        # User not found — create
        def _insert():
            return (
                get_client()
                .table("users")
                .insert(
                    {
                        "telegram_id": telegram_id,
                        "username": username,
                        "first_name": first_name,
                        "language": os.getenv("DEFAULT_LANGUAGE", "ru"),
                        "monthly_budget": float(
                            os.getenv("DEFAULT_MONTHLY_BUDGET", 5_000_000)
                        ),
                        "currency": os.getenv("DEFAULT_CURRENCY", "UZS"),
                    }
                )
                .execute()
            )

        created = await asyncio.to_thread(_insert)
        if created and created.data:
            return created.data[0]

        # Fallback: fetch again (race condition)
        result2 = await asyncio.to_thread(_get)
        if result2 and result2.data:
            return result2.data[0]

        return {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "language": "ru",
            "monthly_budget": 5_000_000,
            "currency": "UZS",
        }
    except Exception as e:
        logger.error("get_or_create_user error telegram_id=%s: %s", telegram_id, e)
        return {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "language": "ru",
            "monthly_budget": 5_000_000,
            "currency": "UZS",
        }


async def save_transaction(
    user_id: int,
    amount: float,
    category: str,
    description: str,
    merchant: str | None,
    ai_advice: str,
    input_type: str = "text",
    purchase_date: str | None = None,
    original_amount: float | None = None,
    original_currency: str | None = None,
    tx_type: str = "expense",
) -> dict:
    try:
        # Validate/normalize an optional explicit date; fall back to today (Tashkent).
        pdate = now_local().date().isoformat()
        if purchase_date:
            try:
                pdate = date.fromisoformat(str(purchase_date)[:10]).isoformat()
            except ValueError:
                pass

        def _insert():
            return (
                get_client()
                .table("transactions")
                .insert(
                    {
                        "user_id": user_id,
                        "amount": amount,
                        "category": category,
                        "description": description,
                        "merchant": merchant,
                        "ai_advice": ai_advice,
                        "input_type": input_type,
                        "purchase_date": pdate,
                        # Stored only when the entry currency differs from the base.
                        "original_amount": original_amount,
                        "original_currency": original_currency,
                        "type": tx_type if tx_type in ("expense", "income") else "expense",
                    }
                )
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("save_transaction error user_id=%s: %s", user_id, e)
        return {}


async def get_monthly_summary_for(user_id: int, month_first: str) -> list[dict]:
    """Category summary for a given month. `month_first` = 'YYYY-MM-01'."""
    try:
        def _select():
            return (
                get_client()
                .table("monthly_summary")
                .select("*")
                .eq("user_id", user_id)
                .eq("month", month_first)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_monthly_summary_for error user_id=%s month=%s: %s", user_id, month_first, e)
        return []


async def get_monthly_summary(user_id: int) -> list[dict]:
    return await get_monthly_summary_for(user_id, now_local().strftime("%Y-%m-01"))


async def get_daily_spent_last_n(user_id: int, n: int = 7) -> list[float]:
    """Returns n daily totals ending today (index 0 = oldest, index n-1 = today).
    Powers the analytics sparkline."""
    try:
        today = now_local().date()
        start = today - timedelta(days=n - 1)

        def _select():
            return (
                get_client()
                .table("transactions")
                .select("amount, purchase_date")
                .eq("user_id", user_id)
                .eq("type", "expense")
                .gte("purchase_date", start.isoformat())
                .execute()
            )

        result = await asyncio.to_thread(_select)
        buckets = [0.0] * n
        for row in (result.data if (result and result.data) else []):
            try:
                d = date.fromisoformat(str(row["purchase_date"])[:10])
                idx = (d - start).days
                if 0 <= idx < n:
                    buckets[idx] += float(row["amount"])
            except (ValueError, TypeError, KeyError):
                continue
        return buckets
    except Exception as e:
        logger.error("get_daily_spent_last_n error user_id=%s: %s", user_id, e)
        return [0.0] * n


async def get_month_spent_through_day(user_id: int, year: int, month: int, day: int) -> float:
    """Sum of spending in {year}-{month} from day 1 through `day` (inclusive).
    Used for the same-day-of-month month-over-month comparison."""
    try:
        first = date(year, month, 1)
        try:
            last = date(year, month, day)
        except ValueError:
            # day out of range for that month (e.g. 31 in Feb) → clamp to month end
            import calendar
            last = date(year, month, calendar.monthrange(year, month)[1])

        def _select():
            return (
                get_client()
                .table("transactions")
                .select("amount")
                .eq("user_id", user_id)
                .eq("type", "expense")
                .gte("purchase_date", first.isoformat())
                .lte("purchase_date", last.isoformat())
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return sum(
            float(row["amount"]) for row in (result.data if (result and result.data) else [])
        )
    except Exception as e:
        logger.error("get_month_spent_through_day error user_id=%s: %s", user_id, e)
        return 0.0


def make_budget_status(budget: float, spent: float) -> dict:
    """Pure helper — build a budget-status dict without touching the DB."""
    budget = float(budget or 0)
    spent = float(spent or 0)
    remaining = max(0.0, budget - spent)
    percent = (spent / budget * 100) if budget > 0 else 0.0
    return {
        "spent": spent,
        "budget": budget,
        "remaining": remaining,
        "percent": percent,
        "warning": percent >= 80,
    }


async def get_month_spent(user_id: int) -> float:
    """Single query: total spent this month."""
    try:
        current_month = now_local().strftime("%Y-%m-01")

        def _get_summary():
            return (
                get_client()
                .table("monthly_summary")
                .select("total_spent")
                .eq("user_id", user_id)
                .eq("month", current_month)
                .execute()
            )

        result = await asyncio.to_thread(_get_summary)
        return sum(
            float(row["total_spent"])
            for row in (result.data if (result and result.data) else [])
        )
    except Exception as e:
        logger.error("get_month_spent error user_id=%s: %s", user_id, e)
        return 0.0


async def get_budget_status(user_id: int, budget: float | None = None) -> dict:
    """Budget status. Pass `budget` (already known from the user row) to skip the
    users lookup and do a single DB round-trip instead of two."""
    try:
        if budget is None:
            def _get_user():
                return (
                    get_client()
                    .table("users")
                    .select("monthly_budget")
                    .eq("telegram_id", user_id)
                    .execute()
                )

            user_result = await asyncio.to_thread(_get_user)
            rows = user_result.data if (user_result and user_result.data) else []
            budget = float(
                rows[0].get("monthly_budget", os.getenv("DEFAULT_MONTHLY_BUDGET", 5_000_000))
                if rows
                else os.getenv("DEFAULT_MONTHLY_BUDGET", 5_000_000)
            )

        spent = await get_month_spent(user_id)
        return make_budget_status(budget, spent)
    except Exception as e:
        logger.error("get_budget_status error user_id=%s: %s", user_id, e)
        return make_budget_status(5_000_000, 0)


async def get_last_transactions(user_id: int, limit: int = 10) -> list[dict]:
    try:
        def _select():
            return (
                get_client()
                .table("transactions")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_last_transactions error user_id=%s: %s", user_id, e)
        return []


async def get_transactions_for_month(user_id: int, month_first: str) -> list[dict]:
    """All transactions within the month starting at `month_first` ('YYYY-MM-01'),
    newest first. Used by the Mini App transactions list (grouped by date)."""
    try:
        start = date.fromisoformat(month_first)
        if start.month == 12:
            nxt = date(start.year + 1, 1, 1)
        else:
            nxt = date(start.year, start.month + 1, 1)

        def _select():
            return (
                get_client()
                .table("transactions")
                .select("*")
                .eq("user_id", user_id)
                .gte("purchase_date", start.isoformat())
                .lt("purchase_date", nxt.isoformat())
                .order("purchase_date", desc=True)
                .order("created_at", desc=True)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_transactions_for_month error user_id=%s month=%s: %s", user_id, month_first, e)
        return []


async def get_user(user_id: int) -> dict:
    """Plain fetch of a user row (no create). Returns {} if not found."""
    try:
        def _select():
            return (
                get_client()
                .table("users")
                .select("*")
                .eq("telegram_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("get_user error user_id=%s: %s", user_id, e)
        return {}


async def update_budget(user_id: int, amount: float) -> None:
    try:
        def _update():
            return (
                get_client()
                .table("users")
                .update({"monthly_budget": amount})
                .eq("telegram_id", user_id)
                .execute()
            )

        await asyncio.to_thread(_update)
    except Exception as e:
        logger.error("update_budget error user_id=%s: %s", user_id, e)


async def update_language(user_id: int, language: str) -> None:
    try:
        def _update():
            return (
                get_client()
                .table("users")
                .update({"language": language})
                .eq("telegram_id", user_id)
                .execute()
            )

        await asyncio.to_thread(_update)
    except Exception as e:
        logger.error("update_language error user_id=%s: %s", user_id, e)


async def update_currency(user_id: int, currency: str) -> None:
    """Change the user's base/display currency (validated by the caller)."""
    try:
        def _update():
            return (
                get_client()
                .table("users")
                .update({"currency": currency})
                .eq("telegram_id", user_id)
                .execute()
            )

        await asyncio.to_thread(_update)
    except Exception as e:
        logger.error("update_currency error user_id=%s: %s", user_id, e)


# ───────────────────────── Notification settings ─────────────────────────

# Sensible defaults: alerts that matter are on; the chatty daily digest is off.
DEFAULT_NOTIFY = {
    "budget_alerts": True,    # 80% / 100% budget thresholds (real-time)
    "large_tx": True,         # unusually large single purchase
    "daily_digest": False,    # evening recap of the day
    "weekly_summary": True,   # Sunday week recap
    "goal_reminders": True,   # savings-goal deadline nudges
    "debt_reminders": True,   # debt/loan due-date nudges
    "payment_reminders": True,  # recurring payment due-tomorrow nudges
}


def notify_settings_of(user: dict) -> dict:
    """Merge a user's stored notify_settings over the defaults."""
    merged = dict(DEFAULT_NOTIFY)
    raw = (user or {}).get("notify_settings") or {}
    if isinstance(raw, dict):
        for k in DEFAULT_NOTIFY:
            if k in raw:
                merged[k] = bool(raw[k])
    return merged


async def update_notify_settings(user_id: int, settings: dict) -> dict:
    """Persist the full (already merged) notify_settings object."""
    clean = {k: bool(settings.get(k, DEFAULT_NOTIFY[k])) for k in DEFAULT_NOTIFY}
    try:
        def _update():
            return (
                get_client()
                .table("users")
                .update({"notify_settings": clean})
                .eq("telegram_id", user_id)
                .execute()
            )

        await asyncio.to_thread(_update)
    except Exception as e:
        logger.error("update_notify_settings error user_id=%s: %s", user_id, e)
    return clean


# ───────────────────────── Notification dedup log ─────────────────────────

async def mark_notif_sent(user_id: int, ntype: str, dedup_key: str) -> bool:
    """Record that a notification was sent. Returns True on first insert, False if
    it already existed (unique constraint) — lets callers treat this as a claim."""
    try:
        def _insert():
            return (
                get_client()
                .table("notifications_log")
                .insert({"user_id": user_id, "type": ntype, "dedup_key": dedup_key})
                .execute()
            )

        await asyncio.to_thread(_insert)
        return True
    except Exception as e:
        # Unique violation == already sent (race or repeat) — not an error worth shouting about.
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            return False
        logger.error("mark_notif_sent error user_id=%s key=%s: %s", user_id, dedup_key, e)
        return False


# ───────────────────────── Date-range queries (digests) ─────────────────────────

async def get_transactions_in_range(user_id: int, start_iso: str, end_iso: str) -> list[dict]:
    """All EXPENSE transactions with purchase_date in [start_iso, end_iso] (inclusive).
    Spend-only — powers digests/analytics, so income is excluded."""
    try:
        def _select():
            return (
                get_client()
                .table("transactions")
                .select("amount, category, purchase_date")
                .eq("user_id", user_id)
                .eq("type", "expense")
                .gte("purchase_date", start_iso)
                .lte("purchase_date", end_iso)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_transactions_in_range error user_id=%s: %s", user_id, e)
        return []


# ───────────────────────── Savings goals ─────────────────────────

async def create_goal(
    user_id: int,
    title: str,
    target_amount: float,
    currency: str,
    emoji: str = "🎯",
    deadline: str | None = None,
    saved_amount: float = 0.0,
) -> dict:
    try:
        dl = None
        if deadline:
            try:
                dl = date.fromisoformat(str(deadline)[:10]).isoformat()
            except ValueError:
                dl = None

        def _insert():
            return (
                get_client()
                .table("goals")
                .insert(
                    {
                        "user_id": user_id,
                        "title": title[:120],
                        "target_amount": float(target_amount),
                        "currency": currency,
                        "emoji": emoji or "🎯",
                        "deadline": dl,
                        "saved_amount": float(saved_amount or 0),
                        "status": "active",
                    }
                )
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("create_goal error user_id=%s: %s", user_id, e)
        return {}


async def get_goals(user_id: int, include_archived: bool = False) -> list[dict]:
    """Active + done goals (done shown so users see their wins), newest first."""
    try:
        def _select():
            q = (
                get_client()
                .table("goals")
                .select("*")
                .eq("user_id", user_id)
            )
            if not include_archived:
                q = q.neq("status", "archived")
            return q.order("created_at", desc=True).execute()

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_goals error user_id=%s: %s", user_id, e)
        return []


async def get_goal(user_id: int, goal_id: int) -> dict:
    try:
        def _select():
            return (
                get_client()
                .table("goals")
                .select("*")
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("get_goal error user_id=%s id=%s: %s", user_id, goal_id, e)
        return {}


async def add_goal_contribution(user_id: int, goal_id: int, amount: float, note: str | None = None) -> dict:
    """Add (or withdraw, if negative) toward a goal. Logs the contribution and
    updates the goal's saved_amount + status. Returns the updated goal (or {})."""
    try:
        goal = await get_goal(user_id, goal_id)
        if not goal:
            return {}

        def _insert():
            return (
                get_client()
                .table("goal_contributions")
                .insert({"goal_id": goal_id, "user_id": user_id, "amount": float(amount), "note": note})
                .execute()
            )

        await asyncio.to_thread(_insert)

        new_saved = max(0.0, float(goal.get("saved_amount", 0) or 0) + float(amount))
        target = float(goal.get("target_amount", 0) or 0)
        status = "done" if (target > 0 and new_saved >= target) else "active"

        def _update():
            return (
                get_client()
                .table("goals")
                .update({"saved_amount": new_saved, "status": status})
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {**goal, "saved_amount": new_saved, "status": status}
    except Exception as e:
        logger.error("add_goal_contribution error user_id=%s id=%s: %s", user_id, goal_id, e)
        return {}


async def update_goal(user_id: int, goal_id: int, fields: dict) -> dict:
    """Patch allowed goal fields (title, emoji, target_amount, deadline, status)."""
    allowed = {"title", "emoji", "target_amount", "deadline", "status", "currency"}
    patch = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not patch:
        return await get_goal(user_id, goal_id)
    try:
        def _update():
            return (
                get_client()
                .table("goals")
                .update(patch)
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("update_goal error user_id=%s id=%s: %s", user_id, goal_id, e)
        return {}


async def delete_goal(user_id: int, goal_id: int) -> bool:
    try:
        def _delete():
            return (
                get_client()
                .table("goals")
                .delete()
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_goal error user_id=%s id=%s: %s", user_id, goal_id, e)
        return False


async def get_all_users() -> list[dict]:
    try:
        def _select():
            return get_client().table("users").select("*").execute()

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_all_users error: %s", e)
        return []


async def delete_transaction(user_id: int, tx_id: int) -> dict:
    """Delete a single transaction. Ownership enforced by user_id filter.
    Returns the deleted row, or {} if nothing was deleted."""
    try:
        def _delete():
            return (
                get_client()
                .table("transactions")
                .delete()
                .eq("id", tx_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("delete_transaction error user_id=%s tx_id=%s: %s", user_id, tx_id, e)
        return {}


async def delete_last_transaction(user_id: int) -> dict:
    """Delete the most recent transaction and return it (or {} if none)."""
    try:
        last = await get_last_transactions(user_id, 1)
        if not last:
            return {}
        deleted = await delete_transaction(user_id, last[0]["id"])
        return deleted or last[0]
    except Exception as e:
        logger.error("delete_last_transaction error user_id=%s: %s", user_id, e)
        return {}


async def count_transactions(user_id: int) -> int:
    """Number of transactions for the user — count-only query (no row payload)."""
    try:
        def _count():
            return (
                get_client()
                .table("transactions")
                .select("id", count="exact")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_count)
        return result.count if (result and result.count is not None) else 0
    except Exception as e:
        logger.error("count_transactions error user_id=%s: %s", user_id, e)
        return 0


# ───────────────────────── Debts / loans ─────────────────────────

async def create_debt(
    user_id: int,
    direction: str,
    counterparty: str,
    amount: float,
    currency: str,
    due_date: str | None = None,
    note: str | None = None,
) -> dict:
    """Create a debt. direction = 'owed_to_me' (they owe me) | 'i_owe' (I owe)."""
    try:
        dd = None
        if due_date:
            try:
                dd = date.fromisoformat(str(due_date)[:10]).isoformat()
            except ValueError:
                dd = None
        if direction not in ("owed_to_me", "i_owe"):
            direction = "owed_to_me"

        def _insert():
            return (
                get_client()
                .table("debts")
                .insert({
                    "user_id": user_id,
                    "direction": direction,
                    "counterparty": (counterparty or "").strip()[:120] or "—",
                    "amount": float(amount),
                    "currency": currency,
                    "due_date": dd,
                    "note": (note or "").strip() or None,
                    "status": "open",
                })
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("create_debt error user_id=%s: %s", user_id, e)
        return {}


async def get_debts(user_id: int, only_open: bool = False) -> list[dict]:
    """Debts for a user. Open first, then by nearest due date."""
    try:
        def _select():
            q = get_client().table("debts").select("*").eq("user_id", user_id)
            if only_open:
                q = q.eq("status", "open")
            return q.order("status", desc=False).order("due_date", desc=False, nullsfirst=False).execute()

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_debts error user_id=%s: %s", user_id, e)
        return []


async def get_debt(user_id: int, debt_id: int) -> dict:
    try:
        def _select():
            return (
                get_client()
                .table("debts")
                .select("*")
                .eq("id", debt_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("get_debt error user_id=%s id=%s: %s", user_id, debt_id, e)
        return {}


async def add_debt_payment(user_id: int, debt_id: int, amount: float) -> dict:
    """Record a (partial) repayment. Bumps paid_amount, clamped to [0, amount];
    auto-settles when fully paid. Returns the updated debt or {}."""
    try:
        debt = await get_debt(user_id, debt_id)
        if not debt:
            return {}
        total = float(debt.get("amount", 0) or 0)
        paid = float(debt.get("paid_amount", 0) or 0) + float(amount)
        paid = max(0.0, min(paid, total))
        status = "settled" if (total > 0 and paid >= total) else "open"

        def _update():
            return (
                get_client()
                .table("debts")
                .update({"paid_amount": paid, "status": status})
                .eq("id", debt_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {**debt, "paid_amount": paid, "status": status}
    except Exception as e:
        logger.error("add_debt_payment error user_id=%s id=%s: %s", user_id, debt_id, e)
        return {}


async def settle_debt(user_id: int, debt_id: int) -> dict:
    """Mark a debt fully settled (paid in full). Returns the updated row or {}."""
    try:
        debt = await get_debt(user_id, debt_id)
        if not debt:
            return {}

        def _update():
            return (
                get_client()
                .table("debts")
                .update({"status": "settled", "paid_amount": float(debt.get("amount", 0) or 0)})
                .eq("id", debt_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("settle_debt error user_id=%s id=%s: %s", user_id, debt_id, e)
        return {}


async def delete_debt(user_id: int, debt_id: int) -> bool:
    try:
        def _delete():
            return (
                get_client()
                .table("debts")
                .delete()
                .eq("id", debt_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_debt error user_id=%s id=%s: %s", user_id, debt_id, e)
        return False


async def delete_all_transactions(user_id: int) -> int:
    """Delete every transaction for the user. Returns the number deleted."""
    try:
        def _count():
            return (
                get_client()
                .table("transactions")
                .select("id", count="exact")
                .eq("user_id", user_id)
                .execute()
            )

        count_res = await asyncio.to_thread(_count)
        total = count_res.count if (count_res and count_res.count is not None) else 0

        def _delete():
            return (
                get_client()
                .table("transactions")
                .delete()
                .eq("user_id", user_id)
                .execute()
            )

        await asyncio.to_thread(_delete)
        return total
    except Exception as e:
        logger.error("delete_all_transactions error user_id=%s: %s", user_id, e)
        return 0


# ───────────────────────── Balance & period reports ─────────────────────────

async def get_balance(user_id: int) -> dict:
    """All-time wallet balance = sum(income) − sum(expense) in the base currency.
    Returns {income, expense, balance}."""
    try:
        def _select():
            return (
                get_client()
                .table("transactions")
                .select("amount, type")
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        income = expense = 0.0
        for row in (result.data if (result and result.data) else []):
            amt = float(row.get("amount") or 0)
            if row.get("type") == "income":
                income += amt
            else:
                expense += amt
        return {"income": income, "expense": expense, "balance": income - expense}
    except Exception as e:
        logger.error("get_balance error user_id=%s: %s", user_id, e)
        return {"income": 0.0, "expense": 0.0, "balance": 0.0}


async def get_period_report(user_id: int, start_iso: str, end_iso: str) -> dict:
    """Income/expense/balance + the operation rows for [start_iso, end_iso] (inclusive),
    newest first. Powers the Mini App Reports (Отчёты) screen."""
    try:
        def _select():
            return (
                get_client()
                .table("transactions")
                .select("*")
                .eq("user_id", user_id)
                .gte("purchase_date", start_iso)
                .lte("purchase_date", end_iso)
                .order("purchase_date", desc=True)
                .order("created_at", desc=True)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        rows = result.data if (result and result.data) else []
        income = sum(float(r.get("amount") or 0) for r in rows if r.get("type") == "income")
        expense = sum(float(r.get("amount") or 0) for r in rows if r.get("type") != "income")
        return {
            "income": income,
            "expense": expense,
            "balance": income - expense,
            "count": len(rows),
            "transactions": rows,
        }
    except Exception as e:
        logger.error("get_period_report error user_id=%s: %s", user_id, e)
        return {"income": 0.0, "expense": 0.0, "balance": 0.0, "count": 0, "transactions": []}


async def get_month_report_data(user_id: int, base_currency: str) -> dict:
    """Aggregate the current month for the bot's monthly overview: running balance,
    income/expense, avg per day, top expense category, tasks done, events passed,
    services paid this month, and the active monthly services cost (base currency)."""
    now = now_local()
    today = now.date()
    month_first = f"{now.year:04d}-{now.month:02d}-01"
    month_prefix = month_first[:7]
    bal, report, payments, tasks, events = await asyncio.gather(
        get_balance(user_id),
        get_period_report(user_id, month_first, today.isoformat()),
        get_payments(user_id),
        get_tasks(user_id),
        get_events_for_month(user_id, month_first),
    )
    cats: dict[str, float] = {}
    for r in report["transactions"]:
        if r.get("type") == "income":
            continue
        c = r.get("category", "Другое")
        cats[c] = cats.get(c, 0.0) + float(r.get("amount") or 0)
    top_category = max(cats, key=cats.get) if cats else None
    tasks_done = sum(
        1 for t in tasks
        if t.get("status") == "done" and str(t.get("completed_at") or "")[:7] == month_prefix
    )
    events_done = sum(1 for e in events if str(e.get("event_date") or "")[:10] <= today.isoformat())
    services_paid = sum(1 for p in payments if str(p.get("last_paid_date") or "")[:7] == month_prefix)
    mult = {"weekly": 52 / 12, "monthly": 1.0, "yearly": 1 / 12}
    services_monthly = 0.0
    for p in payments:
        if p.get("status") != "active":
            continue
        amt = await convert(float(p.get("amount") or 0), normalize_currency(p.get("currency"), base_currency), base_currency)
        services_monthly += amt * mult.get(p.get("period", "monthly"), 1.0)
    return {
        "balance": bal["balance"],
        "income": report["income"],
        "expense": report["expense"],
        "avg_day": report["expense"] / (today.day or 1),
        "top_category": top_category,
        "tasks_done": tasks_done,
        "events_done": events_done,
        "services_paid": services_paid,
        "services_monthly": services_monthly,
        "has_activity": bool(report["income"] or report["expense"] or payments or tasks or events),
    }


# ───────────────────────── Calendar events ─────────────────────────

async def create_event(
    user_id: int,
    title: str,
    event_date: str,
    event_time: str | None = None,
    note: str | None = None,
    emoji: str = "📌",
) -> dict:
    try:
        try:
            ed = date.fromisoformat(str(event_date)[:10]).isoformat()
        except (ValueError, TypeError):
            ed = now_local().date().isoformat()

        def _insert():
            return (
                get_client()
                .table("events")
                .insert({
                    "user_id": user_id,
                    "title": (title or "").strip()[:200] or "—",
                    "event_date": ed,
                    "event_time": (event_time or None),
                    "note": (note or "").strip() or None,
                    "emoji": emoji or "📌",
                })
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("create_event error user_id=%s: %s", user_id, e)
        return {}


async def get_events_for_month(user_id: int, month_first: str) -> list[dict]:
    """All events with event_date inside the month starting at `month_first`."""
    try:
        start = date.fromisoformat(month_first)
        nxt = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)

        def _select():
            return (
                get_client()
                .table("events")
                .select("*")
                .eq("user_id", user_id)
                .gte("event_date", start.isoformat())
                .lt("event_date", nxt.isoformat())
                .order("event_date", desc=False)
                .order("event_time", desc=False, nullsfirst=True)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_events_for_month error user_id=%s month=%s: %s", user_id, month_first, e)
        return []


async def get_upcoming_events(user_id: int, limit: int = 5) -> list[dict]:
    """Events from today forward, soonest first — for the overview glance."""
    try:
        today = now_local().date().isoformat()

        def _select():
            return (
                get_client()
                .table("events")
                .select("*")
                .eq("user_id", user_id)
                .gte("event_date", today)
                .order("event_date", desc=False)
                .order("event_time", desc=False, nullsfirst=True)
                .limit(limit)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_upcoming_events error user_id=%s: %s", user_id, e)
        return []


async def get_all_events(user_id: int) -> list[dict]:
    """Every event for a user (used by the data export)."""
    try:
        def _select():
            return (
                get_client()
                .table("events")
                .select("*")
                .eq("user_id", user_id)
                .order("event_date", desc=False)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_all_events error user_id=%s: %s", user_id, e)
        return []


async def update_event(user_id: int, event_id: int, fields: dict) -> dict:
    allowed = {"title", "event_date", "event_time", "note", "emoji"}
    patch = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not patch:
        return {}
    try:
        def _update():
            return (
                get_client()
                .table("events")
                .update(patch)
                .eq("id", event_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("update_event error user_id=%s id=%s: %s", user_id, event_id, e)
        return {}


async def delete_event(user_id: int, event_id: int) -> bool:
    try:
        def _delete():
            return (
                get_client()
                .table("events")
                .delete()
                .eq("id", event_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_event error user_id=%s id=%s: %s", user_id, event_id, e)
        return False


# ───────────────────────── Recurring payments ─────────────────────────

def _advance_due(d: date, period: str, anchor_day: int | None = None) -> date:
    """Next due date after one period. `anchor_day` preserves the intended billing
    day across short months (e.g. the 31st), clamping only when the month is shorter."""
    if period == "weekly":
        return d + timedelta(days=7)
    import calendar as _cal
    day = anchor_day or d.day
    if period == "yearly":
        last = _cal.monthrange(d.year + 1, d.month)[1]
        return date(d.year + 1, d.month, min(day, last))
    # monthly (default)
    m = d.month + 1
    y = d.year + (1 if m > 12 else 0)
    m = 1 if m > 12 else m
    last = _cal.monthrange(y, m)[1]
    return date(y, m, min(day, last))


async def create_payment(
    user_id: int,
    name: str,
    category: str,
    amount: float,
    currency: str,
    period: str = "monthly",
    next_due_date: str | None = None,
    note: str | None = None,
) -> dict:
    try:
        period = period if period in ("weekly", "monthly", "yearly") else "monthly"
        try:
            ndd = date.fromisoformat(str(next_due_date)[:10]) if next_due_date else now_local().date()
        except (ValueError, TypeError):
            ndd = now_local().date()

        def _insert():
            return (
                get_client()
                .table("payments")
                .insert({
                    "user_id": user_id,
                    "name": (name or "").strip()[:120] or "—",
                    "category": (category or "Подписка").strip()[:60] or "Подписка",
                    "amount": float(amount),
                    "currency": currency,
                    "period": period,
                    "next_due_date": ndd.isoformat(),
                    "note": (note or "").strip() or None,
                    "status": "active",
                })
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("create_payment error user_id=%s: %s", user_id, e)
        return {}


async def get_payments(user_id: int) -> list[dict]:
    """All recurring payments, soonest due first."""
    try:
        def _select():
            return (
                get_client()
                .table("payments")
                .select("*")
                .eq("user_id", user_id)
                .order("next_due_date", desc=False)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_payments error user_id=%s: %s", user_id, e)
        return []


async def get_payment(user_id: int, payment_id: int) -> dict:
    try:
        def _select():
            return (
                get_client()
                .table("payments")
                .select("*")
                .eq("id", payment_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("get_payment error user_id=%s id=%s: %s", user_id, payment_id, e)
        return {}


async def update_payment(user_id: int, payment_id: int, fields: dict) -> dict:
    allowed = {"name", "category", "amount", "currency", "period", "next_due_date", "note", "status"}
    patch = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not patch:
        return await get_payment(user_id, payment_id)
    try:
        def _update():
            return (
                get_client()
                .table("payments")
                .update(patch)
                .eq("id", payment_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("update_payment error user_id=%s id=%s: %s", user_id, payment_id, e)
        return {}


async def mark_payment_paid(user_id: int, payment_id: int) -> dict:
    """Mark the current cycle paid: stamp last_paid_date=today and roll next_due_date
    forward by one period. Returns the updated payment (or {})."""
    try:
        p = await get_payment(user_id, payment_id)
        if not p:
            return {}
        today = now_local().date()
        try:
            cur_due = date.fromisoformat(str(p.get("next_due_date"))[:10])
        except (ValueError, TypeError):
            cur_due = today
        anchor = cur_due.day  # keep the original billing day across short months
        nxt = _advance_due(cur_due, p.get("period", "monthly"), anchor_day=anchor)
        # Never leave the next due date in the past (e.g. long-overdue payment).
        while nxt <= today:
            nxt = _advance_due(nxt, p.get("period", "monthly"), anchor_day=anchor)

        def _update():
            return (
                get_client()
                .table("payments")
                .update({"last_paid_date": today.isoformat(), "next_due_date": nxt.isoformat()})
                .eq("id", payment_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("mark_payment_paid error user_id=%s id=%s: %s", user_id, payment_id, e)
        return {}


async def delete_payment(user_id: int, payment_id: int) -> bool:
    try:
        def _delete():
            return (
                get_client()
                .table("payments")
                .delete()
                .eq("id", payment_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_payment error user_id=%s id=%s: %s", user_id, payment_id, e)
        return False


# ───────────────────────── Task folders ─────────────────────────

async def create_task_folder(user_id: int, name: str, emoji: str = "📁") -> dict:
    try:
        def _insert():
            return (
                get_client()
                .table("task_folders")
                .insert({"user_id": user_id, "name": (name or "").strip()[:80] or "—", "emoji": emoji or "📁"})
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("create_task_folder error user_id=%s: %s", user_id, e)
        return {}


async def get_task_folders(user_id: int) -> list[dict]:
    try:
        def _select():
            return (
                get_client()
                .table("task_folders")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=False)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_task_folders error user_id=%s: %s", user_id, e)
        return []


async def delete_task_folder(user_id: int, folder_id: int) -> bool:
    """Delete a folder. Tasks in it keep existing (folder_id set null via FK)."""
    try:
        def _delete():
            return (
                get_client()
                .table("task_folders")
                .delete()
                .eq("id", folder_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_task_folder error user_id=%s id=%s: %s", user_id, folder_id, e)
        return False


# ───────────────────────── Tasks ─────────────────────────

async def create_task(
    user_id: int,
    title: str,
    priority: str | None = None,
    due_date: str | None = None,
    folder_id: int | None = None,
    tags: list | None = None,
    note: str | None = None,
) -> dict:
    try:
        dd = None
        if due_date:
            try:
                dd = date.fromisoformat(str(due_date)[:10]).isoformat()
            except (ValueError, TypeError):
                dd = None
        prio = priority if priority in ("critical", "high", "medium", "low") else None
        clean_tags = [str(t).strip()[:30] for t in (tags or []) if str(t).strip()][:10]

        def _insert():
            return (
                get_client()
                .table("tasks")
                .insert({
                    "user_id": user_id,
                    "title": (title or "").strip()[:200] or "—",
                    "status": "active",
                    "priority": prio,
                    "due_date": dd,
                    "folder_id": folder_id,
                    "tags": clean_tags,
                    "note": (note or "").strip() or None,
                })
                .execute()
            )

        result = await asyncio.to_thread(_insert)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("create_task error user_id=%s: %s", user_id, e)
        return {}


async def get_tasks(user_id: int) -> list[dict]:
    """All tasks, newest first. The Mini App filters by status/priority/folder client-side."""
    try:
        def _select():
            return (
                get_client()
                .table("tasks")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data if (result and result.data) else []
    except Exception as e:
        logger.error("get_tasks error user_id=%s: %s", user_id, e)
        return []


async def get_task(user_id: int, task_id: int) -> dict:
    try:
        def _select():
            return (
                get_client()
                .table("tasks")
                .select("*")
                .eq("id", task_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("get_task error user_id=%s id=%s: %s", user_id, task_id, e)
        return {}


async def update_task(user_id: int, task_id: int, fields: dict) -> dict:
    """Patch allowed task fields. Setting status='done' stamps completed_at;
    moving back to active/cancelled clears it."""
    allowed = {"title", "status", "priority", "due_date", "folder_id", "tags", "note"}
    patch = {k: v for k, v in (fields or {}).items() if k in allowed}
    if "status" in patch:
        if patch["status"] == "done":
            patch["completed_at"] = now_local().isoformat()
        else:
            patch["completed_at"] = None
    if not patch:
        return await get_task(user_id, task_id)
    try:
        def _update():
            return (
                get_client()
                .table("tasks")
                .update(patch)
                .eq("id", task_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_update)
        return result.data[0] if (result and result.data) else {}
    except Exception as e:
        logger.error("update_task error user_id=%s id=%s: %s", user_id, task_id, e)
        return {}


async def delete_task(user_id: int, task_id: int) -> bool:
    try:
        def _delete():
            return (
                get_client()
                .table("tasks")
                .delete()
                .eq("id", task_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_task error user_id=%s id=%s: %s", user_id, task_id, e)
        return False


# ───────────────────────── Account data management ─────────────────────────

async def wipe_user_data(user_id: int) -> dict:
    """Delete all of a user's content (transactions, goals, debts, events,
    payments, tasks, folders) but keep the account row. Returns per-table counts."""
    tables = ["transactions", "goals", "debts", "events", "payments", "tasks", "task_folders"]
    counts = {}
    for t in tables:
        try:
            def _delete(tbl=t):
                return (
                    get_client()
                    .table(tbl)
                    .delete(count="exact")
                    .eq("user_id", user_id)
                    .execute()
                )

            result = await asyncio.to_thread(_delete)
            counts[t] = result.count if (result and result.count is not None) else (len(result.data) if (result and result.data) else 0)
        except Exception as e:
            logger.error("wipe_user_data error user_id=%s table=%s: %s", user_id, t, e)
            counts[t] = 0
    return counts


async def delete_account(user_id: int) -> bool:
    """Delete the user row. All child rows cascade via FK (on delete cascade)."""
    try:
        def _delete():
            return (
                get_client()
                .table("users")
                .delete()
                .eq("telegram_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        return bool(result and result.data)
    except Exception as e:
        logger.error("delete_account error user_id=%s: %s", user_id, e)
        return False
