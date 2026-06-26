"""Multi-currency support: currency catalogue + live exchange rates.

Design
------
Every user has a single *base* currency (stored on `users.currency`). All budget
and analytics math is done in that base currency, so a transaction entered in a
foreign currency is converted to the base at save time and the base amount is
what lands in `transactions.amount` (the `monthly_summary` view keeps working
unchanged). The original amount/currency are stored alongside for transparency.

Rates are fetched once from a keyless public API (open.er-api.com) relative to
USD and cached in-memory for `RATE_TTL` seconds. Cross rates are derived from the
USD table, so any pair works even when the API doesn't support a base directly.
If the network call fails we fall back to a small static table — the bot stays
functional (UZS-only users never touch the network at all).
"""
import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# ── Currency catalogue ──────────────────────────────────────────────
# pos: where the symbol sits ("suffix" → "1 000 сум", "prefix" → "$1,000").
# dec: how many fraction digits to show. grp: thousands separator.
CURRENCIES = {
    "UZS": {"symbol": "сум", "symbol_en": "so'm", "pos": "suffix", "dec": 0, "grp": " ", "flag": "🇺🇿", "name_ru": "Узбекский сум", "name_en": "Uzbek soʼm"},
    "USD": {"symbol": "$",   "symbol_en": "$",    "pos": "prefix", "dec": 2, "grp": ",", "flag": "🇺🇸", "name_ru": "Доллар США",    "name_en": "US Dollar"},
    "EUR": {"symbol": "€",   "symbol_en": "€",    "pos": "prefix", "dec": 2, "grp": ",", "flag": "🇪🇺", "name_ru": "Евро",           "name_en": "Euro"},
    "RUB": {"symbol": "₽",   "symbol_en": "₽",    "pos": "suffix", "dec": 0, "grp": " ", "flag": "🇷🇺", "name_ru": "Российский рубль","name_en": "Russian Ruble"},
    "KZT": {"symbol": "₸",   "symbol_en": "₸",    "pos": "suffix", "dec": 0, "grp": " ", "flag": "🇰🇿", "name_ru": "Казахский тенге", "name_en": "Kazakh Tenge"},
    "TRY": {"symbol": "₺",   "symbol_en": "₺",    "pos": "prefix", "dec": 0, "grp": " ", "flag": "🇹🇷", "name_ru": "Турецкая лира",   "name_en": "Turkish Lira"},
    "GBP": {"symbol": "£",   "symbol_en": "£",    "pos": "prefix", "dec": 2, "grp": ",", "flag": "🇬🇧", "name_ru": "Фунт стерлингов", "name_en": "British Pound"},
    "AED": {"symbol": "AED", "symbol_en": "AED",  "pos": "suffix", "dec": 2, "grp": ",", "flag": "🇦🇪", "name_ru": "Дирхам ОАЭ",      "name_en": "UAE Dirham"},
}

DEFAULT_CURRENCY = "UZS"

# Map common symbols / words → ISO code, so LLM output and free text both normalize.
SYMBOL_TO_CODE = {
    "$": "USD", "€": "EUR", "₽": "RUB", "₸": "KZT", "₺": "TRY", "£": "GBP",
    "сум": "UZS", "soat": "UZS", "so'm": "UZS", "soum": "UZS", "руб": "RUB",
    "доллар": "USD", "евро": "EUR", "тенге": "KZT", "лир": "TRY", "дирхам": "AED",
    "usd": "USD", "eur": "EUR", "rub": "RUB", "kzt": "KZT", "try": "TRY",
    "uzs": "UZS", "gbp": "GBP", "aed": "AED",
}

# Units of currency per 1 USD. Fallback only (approximate, mid-2026 ballpark).
_FALLBACK_USD = {
    "USD": 1.0, "UZS": 12600.0, "EUR": 0.92, "RUB": 91.0,
    "KZT": 470.0, "TRY": 33.0, "GBP": 0.79, "AED": 3.67,
}

RATE_TTL = 6 * 3600  # refresh live rates at most every 6 hours
_RATES_API = "https://open.er-api.com/v6/latest/USD"

# module-level cache: {"ts": epoch, "usd": {code: per_usd}}
_cache: dict = {"ts": 0.0, "usd": dict(_FALLBACK_USD)}
_lock = asyncio.Lock()


def normalize_currency(value, default: str = DEFAULT_CURRENCY) -> str:
    """Coerce arbitrary user/LLM input into a supported ISO code."""
    if not value:
        return default
    s = str(value).strip()
    up = s.upper()
    if up in CURRENCIES:
        return up
    low = s.lower()
    for token, code in SYMBOL_TO_CODE.items():
        if token in low:
            return code
    return default


def _fetch_usd_table() -> dict:
    """Blocking fetch of USD-based rates. Returns {} on any failure."""
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(_RATES_API)
            r.raise_for_status()
            data = r.json()
        if data.get("result") != "success":
            return {}
        rates = data.get("rates") or {}
        # Keep only the currencies we support and that are present.
        table = {c: float(rates[c]) for c in CURRENCIES if c in rates and rates[c]}
        return table if "USD" in table else {}
    except Exception as e:
        logger.warning("currency rate fetch failed: %s", e)
        return {}


async def _usd_table() -> dict:
    """Return a fresh-enough USD-based rate table, fetching if the cache is stale."""
    now = time.time()
    if (now - _cache["ts"]) < RATE_TTL and _cache["usd"]:
        return _cache["usd"]
    async with _lock:
        # Re-check after acquiring the lock (another coroutine may have refreshed).
        if (time.time() - _cache["ts"]) < RATE_TTL and _cache["usd"] != _FALLBACK_USD:
            return _cache["usd"]
        table = await asyncio.to_thread(_fetch_usd_table)
        if table:
            # Backfill any missing supported currency from the fallback table.
            merged = dict(_FALLBACK_USD)
            merged.update(table)
            _cache["usd"] = merged
            _cache["ts"] = time.time()
            logger.info("currency rates refreshed (%d currencies)", len(table))
        else:
            # Keep serving whatever we have; nudge ts so we don't hammer the API.
            _cache["ts"] = time.time() - RATE_TTL + 600  # retry in ~10 min
    return _cache["usd"]


async def convert(amount: float, from_cur: str, to_cur: str) -> float:
    """Convert `amount` from one supported currency to another via the USD table."""
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    f = normalize_currency(from_cur)
    t = normalize_currency(to_cur)
    if f == t or amount == 0:
        return amount
    table = await _usd_table()
    per_usd_from = table.get(f) or _FALLBACK_USD.get(f, 1.0)
    per_usd_to = table.get(t) or _FALLBACK_USD.get(t, 1.0)
    if not per_usd_from:
        return amount
    usd = amount / per_usd_from
    return usd * per_usd_to


def currency_meta(code: str) -> dict:
    return CURRENCIES.get(normalize_currency(code), CURRENCIES[DEFAULT_CURRENCY])
