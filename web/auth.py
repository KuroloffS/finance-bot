"""Validation of Telegram Mini App initData.

Security: the frontend sends Telegram WebApp `initData` (a query string signed by
Telegram with the bot token). We verify the HMAC so a client cannot forge another
user's identity. The authenticated user id is taken ONLY from the verified payload,
never from the request body.

Algorithm (per Telegram docs):
  data_check_string = "\n".join(sorted("key=value" for all fields except `hash`))
  secret_key        = HMAC_SHA256(key="WebAppData", msg=bot_token)
  expected_hash     = HMAC_SHA256(key=secret_key, msg=data_check_string)  (hex)
  valid             = expected_hash == hash
"""
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)

# initData older than this is rejected (replay-window guard). 24h default.
MAX_AGE_SECONDS = int(os.getenv("WEBAPP_INITDATA_MAX_AGE", 86400))


def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def validate_init_data(init_data: str, max_age: int = MAX_AGE_SECONDS) -> dict | None:
    """Return the parsed, verified user dict (with at least 'id') or None if invalid.

    `init_data` is the raw string from Telegram.WebApp.initData.
    """
    if not init_data:
        return None
    token = _bot_token()
    if not token:
        logger.error("validate_init_data: TELEGRAM_BOT_TOKEN not set")
        return None

    try:
        # Keep raw values; do NOT unquote twice. parse_qsl already unquotes once,
        # which matches how Telegram builds the data_check_string.
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception as e:
        logger.warning("validate_init_data parse error: %s", e)
        return None

    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        logger.warning("validate_init_data: hash mismatch")
        return None

    # Freshness / replay-window guard
    try:
        auth_date = int(data.get("auth_date", "0"))
        if max_age and auth_date and (time.time() - auth_date) > max_age:
            logger.warning("validate_init_data: stale auth_date")
            return None
    except (ValueError, TypeError):
        return None

    # Extract the user object
    user_raw = data.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None

    return user
