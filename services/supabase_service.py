import asyncio
import logging
import os
from datetime import date, datetime, timedelta

import pytz
from supabase import create_client, Client

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
    """All transactions with purchase_date in [start_iso, end_iso] (inclusive)."""
    try:
        def _select():
            return (
                get_client()
                .table("transactions")
                .select("amount, category, purchase_date")
                .eq("user_id", user_id)
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


async def settle_debt(user_id: int, debt_id: int) -> dict:
    """Mark a debt as settled (paid back). Returns the updated row or {}."""
    try:
        def _update():
            return (
                get_client()
                .table("debts")
                .update({"status": "settled"})
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
