import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import time

from groq import Groq

from services.currency_service import (
    CURRENCIES,
    DEFAULT_CURRENCY,
    currency_meta,
    normalize_currency,
)

logger = logging.getLogger(__name__)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# --- Models (verified live against Groq API, June 2026) ---
TEXT_MODEL = "llama-3.3-70b-versatile"          # smart parsing & categorization
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # multimodal receipt OCR
WHISPER_MODEL = "whisper-large-v3"              # voice / audio transcription

CATEGORIES = [
    "Продукты",
    "Кафе и рестораны",
    "Транспорт",
    "Жильё и коммуналка",
    "Здоровье",
    "Развлечения",
    "Шоппинг",
    "Работа и бизнес",
    "Другое",
]

_FALLBACK = {
    "amount": 0,
    "category": "Другое",
    "merchant": None,
    "description": "",
    "advice": "",
    "currency": DEFAULT_CURRENCY,
}


def _to_float(value) -> float:
    """Robustly convert '45 000', '45,000', '45000.50', 45000 → float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    # keep digits, dot and comma; drop spaces and currency words
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return 0.0
    # if both separators present, assume comma = thousands
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        # comma as decimal only if it looks like decimals (1-2 trailing digits)
        if re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _normalize(result: dict, default_desc: str, base_currency: str = DEFAULT_CURRENCY) -> dict:
    result.setdefault("amount", 0)
    result.setdefault("category", "Другое")
    result.setdefault("merchant", None)
    result.setdefault("description", default_desc)
    result.setdefault("advice", "")
    if result["category"] not in CATEGORIES:
        result["category"] = "Другое"
    result["amount"] = _to_float(result["amount"])
    if isinstance(result.get("merchant"), str) and not result["merchant"].strip():
        result["merchant"] = None
    # Currency: normalize to a supported ISO code, defaulting to the user's base.
    result["currency"] = normalize_currency(result.get("currency"), base_currency)
    return result


async def parse_text_purchase(text: str, user_context: dict) -> dict:
    lang = user_context.get("language", "ru")
    budget = user_context.get("monthly_budget", 5_000_000)
    spent = user_context.get("spent_this_month", 0)
    base_currency = normalize_currency(user_context.get("currency"), DEFAULT_CURRENCY)
    base_meta = currency_meta(base_currency)
    cur_word = base_meta["symbol"]
    codes_str = "/".join(CURRENCIES.keys())
    categories_str = ", ".join(CATEGORIES)

    if lang == "ru":
        system_prompt = (
            "Ты — внимательный финансовый помощник. Извлеки данные о трате из сообщения.\n"
            f"Валюта по умолчанию: {base_currency} ({cur_word}). Суммы словами «двадцать пять тысяч», "
            "«полтора миллиона» переводи в число.\n"
            "Если в сообщении явно указана ДРУГАЯ валюта (например $, доллары, евро, €, рубли, ₽, тенге), "
            f"укажи её ISO-код в поле currency (одно из: {codes_str}), а amount — в этой валюте. "
            f"Иначе currency = {base_currency}.\n"
            f"Контекст: месячный бюджет {budget:.0f}, уже потрачено {spent:.0f} (в {base_currency}).\n"
            f"Категория — строго одна из: {categories_str}.\n"
            "Если в сообщении нет траты или суммы — верни amount = 0.\n"
            "Поле merchant — название магазина/заведения, если упомянуто, иначе null.\n"
            "advice — один короткий практичный совет по этой трате (одно предложение, по-дружески).\n"
            'Ответь строго JSON: {"amount": <число>, "currency": "<ISO>", "category": "<категория>", '
            '"merchant": "<строка|null>", "description": "<краткое описание траты>", "advice": "<совет>"}'
        )
    else:
        system_prompt = (
            "You are an attentive financial assistant. Extract purchase data from the message.\n"
            f"Default currency: {base_currency} ({cur_word}). Convert worded amounts into a number.\n"
            "If the message explicitly names ANOTHER currency (e.g. $, dollars, euro, €, rubles, ₽, tenge), "
            f"put its ISO code in the currency field (one of: {codes_str}) and give amount in that currency. "
            f"Otherwise currency = {base_currency}.\n"
            f"Context: monthly budget {budget:.0f}, already spent {spent:.0f} (in {base_currency}).\n"
            f"Category — strictly one of: {categories_str}.\n"
            "If there is no purchase or amount — return amount = 0.\n"
            "merchant — store/place name if mentioned, otherwise null.\n"
            "advice — one short practical tip about this purchase (single friendly sentence).\n"
            'Reply strictly JSON: {"amount": <number>, "currency": "<ISO>", "category": "<category>", '
            '"merchant": "<string|null>", "description": "<short description>", "advice": "<tip>"}'
        )

    raw = ""
    try:
        def _call():
            return client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )

        response = await asyncio.to_thread(_call)
        raw = response.choices[0].message.content
        logger.info("parse_text -> %s", raw[:200])
        return _normalize(_extract_json(raw), text, base_currency)
    except Exception as e:
        logger.error("parse_text_purchase error: %s | raw=%s", e, raw[:300])
        return {**_FALLBACK, "description": text, "currency": base_currency}


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Transcribe voice/audio. Groq Whisper accepts OGG/MP3/M4A directly — no ffmpeg needed.
    Language is auto-detected (handles Russian + Uzbek)."""
    suffix = os.path.splitext(filename)[1] or ".ogg"
    tmp_path = os.path.join(tempfile.gettempdir(), f"audio_{int(time.time())}{suffix}")
    try:
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)

        def _transcribe():
            with open(tmp_path, "rb") as f:
                return client.audio.transcriptions.create(
                    file=(os.path.basename(tmp_path), f),
                    model=WHISPER_MODEL,
                )

        transcription = await asyncio.to_thread(_transcribe)
        text = (transcription.text or "").strip()
        logger.info("transcribe_audio -> %s", text[:120])
        return text
    except Exception as e:
        logger.error("transcribe_audio error: %s", e)
        return ""
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def parse_photo_receipt(image_bytes: bytes, user_context: dict) -> dict:
    raw = ""
    try:
        if image_bytes[:2] == b"\xff\xd8":
            mime_type = "image/jpeg"
        elif image_bytes[:4] == b"\x89PNG":
            mime_type = "image/png"
        elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            mime_type = "image/webp"
        else:
            mime_type = "image/jpeg"

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        lang = user_context.get("language", "ru")
        base_currency = normalize_currency(user_context.get("currency"), DEFAULT_CURRENCY)
        codes_str = "/".join(CURRENCIES.keys())
        categories_str = ", ".join(CATEGORIES)

        if lang == "ru":
            prompt_text = (
                "На фото чек, квитанция или ценник. Извлеки ИТОГОВУЮ сумму к оплате "
                "(только число, без пробелов и валюты), название магазина/заведения "
                f"и категорию строго из списка: {categories_str}. "
                "Если итог не виден — посчитай сумму позиций. "
                f"Определи валюту чека (символ/код) и верни её ISO-код в поле currency (одно из: {codes_str}); "
                f"если валюта не ясна — используй {base_currency}. "
                "Ответь строго JSON без markdown: "
                '{"amount": <число>, "currency": "<ISO>", "category": "<категория>", "merchant": "<строка|null>", '
                '"description": "<что куплено>", "advice": "<короткий совет>"}'
            )
        else:
            prompt_text = (
                "The photo is a receipt or price tag. Extract the TOTAL amount due "
                "(number only, no spaces/currency), the store/place name, "
                f"and a category strictly from: {categories_str}. "
                f"Detect the receipt currency (symbol/code) and return its ISO code in the currency field "
                f"(one of: {codes_str}); if unclear, use {base_currency}. "
                "Reply strictly JSON, no markdown: "
                '{"amount": <number>, "currency": "<ISO>", "category": "<category>", "merchant": "<string|null>", '
                '"description": "<what was bought>", "advice": "<short tip>"}'
            )

        def _call():
            return client.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                            },
                            {"type": "text", "text": prompt_text},
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=600,
                response_format={"type": "json_object"},
            )

        response = await asyncio.to_thread(_call)
        raw = response.choices[0].message.content
        logger.info("parse_photo -> %s", raw[:200])
        return _normalize(_extract_json(raw), "Чек", base_currency)
    except Exception as e:
        logger.error("parse_photo_receipt error: %s | raw=%s", e, raw[:300])
        return {**_FALLBACK, "description": "Чек"}


async def parse_goal_text(text: str, user_context: dict) -> dict:
    """Extract a savings goal {title, amount, currency, deadline} from free text or
    transcribed speech. Resolves relative deadlines ('к декабрю', 'через год')
    against the supplied 'today'."""
    lang = user_context.get("language", "ru")
    base_currency = normalize_currency(user_context.get("currency"), DEFAULT_CURRENCY)
    today = user_context.get("today", "")
    codes_str = "/".join(CURRENCIES.keys())

    if lang == "ru":
        system_prompt = (
            f"Извлеки данные цели накопления из сообщения. Сегодня {today}.\n"
            "Верни строго JSON: title (короткое название цели, например «Отпуск»), "
            "amount (целевая сумма — число), "
            f"currency (ISO-код, одно из {codes_str}; по умолчанию {base_currency}), "
            "deadline (дата в формате ГГГГ-ММ-ДД или null).\n"
            "Относительные сроки («к декабрю», «через год», «до лета», «через 3 месяца») "
            "преобразуй в конкретную дату относительно сегодняшней. "
            "Суммы словами («десять миллионов», «полтора млн») переведи в число. "
            "Если суммы нет — amount = 0.\n"
            '{"title": "<строка>", "amount": <число>, "currency": "<ISO>", "deadline": "<ГГГГ-ММ-ДД|null>"}'
        )
    else:
        system_prompt = (
            f"Extract a savings-goal from the message. Today is {today}.\n"
            "Return strictly JSON: title (short goal name e.g. 'Vacation'), "
            "amount (target amount as a number), "
            f"currency (ISO code, one of {codes_str}; default {base_currency}), "
            "deadline (date YYYY-MM-DD or null).\n"
            "Convert relative deadlines ('by December', 'in a year', 'in 3 months') to a concrete "
            "date relative to today. Convert worded amounts into a number. If no amount — amount = 0.\n"
            '{"title": "<string>", "amount": <number>, "currency": "<ISO>", "deadline": "<YYYY-MM-DD|null>"}'
        )

    raw = ""
    try:
        def _call():
            return client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
            )

        response = await asyncio.to_thread(_call)
        raw = response.choices[0].message.content
        logger.info("parse_goal -> %s", raw[:200])
        data = _extract_json(raw)
        title = str(data.get("title") or "").strip()[:120]
        amount = _to_float(data.get("amount"))
        currency = normalize_currency(data.get("currency"), base_currency)
        deadline = None
        dl = data.get("deadline")
        if dl and str(dl).lower() not in ("null", "none", ""):
            try:
                import datetime as _dt
                deadline = _dt.date.fromisoformat(str(dl)[:10]).isoformat()
            except ValueError:
                deadline = None
        return {"title": title, "amount": amount, "currency": currency, "deadline": deadline}
    except Exception as e:
        logger.error("parse_goal_text error: %s | raw=%s", e, raw[:200])
        return {"title": "", "amount": 0.0, "currency": base_currency, "deadline": None}


async def parse_amount_text(text: str, base_currency: str = DEFAULT_CURRENCY) -> float:
    """Best-effort: turn a spoken/worded amount ('двести тысяч', 'five hundred')
    into a number. Used as a fallback when plain digit parsing fails (e.g. voice)."""
    raw = ""
    try:
        def _call():
            return client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": (
                        "Extract the monetary amount from the message as a plain number. "
                        "Convert worded amounts (e.g. 'двести тысяч', 'десять миллионов', "
                        "'five hundred') into digits. Reply strictly JSON: {\"amount\": <number>}. "
                        "If there is no amount, return 0."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=60,
                response_format={"type": "json_object"},
            )

        response = await asyncio.to_thread(_call)
        raw = response.choices[0].message.content
        return _to_float(_extract_json(raw).get("amount"))
    except Exception as e:
        logger.error("parse_amount_text error: %s | raw=%s", e, raw[:120])
        return 0.0


async def parse_debt_text(text: str, user_context: dict) -> dict:
    """Extract a debt from free text / speech: who, how much, which way, by when.
    direction='owed_to_me' when someone owes the user, 'i_owe' when the user owes.
    Resolves relative deadlines against the supplied 'today'."""
    lang = user_context.get("language", "ru")
    base_currency = normalize_currency(user_context.get("currency"), DEFAULT_CURRENCY)
    today = user_context.get("today", "")
    codes_str = "/".join(CURRENCIES.keys())

    if lang == "ru":
        system_prompt = (
            f"Извлеки данные о долге/займе из сообщения. Сегодня {today}.\n"
            "direction: 'owed_to_me' если ДОЛЖНЫ пользователю («Али должен мне», «дал в долг Али», "
            "«мне должен»); 'i_owe' если ДОЛЖЕН пользователь («я должен», «занял у», «взял у», «должен Али»).\n"
            "counterparty — имя человека/компании. amount — сумма числом (слова «пятьсот тысяч», "
            "«пол миллиона», «500к» переведи в число). "
            f"currency — ISO-код (одно из {codes_str}; по умолчанию {base_currency}). "
            "deadline — дата ГГГГ-ММ-ДД или null; относительные сроки («до 5 июля», «через неделю», "
            "«к декабрю») переведи в конкретную дату относительно сегодня.\n"
            'Ответь строго JSON: {"direction":"owed_to_me|i_owe","counterparty":"<строка>",'
            '"amount":<число>,"currency":"<ISO>","deadline":"<ГГГГ-ММ-ДД|null>"}'
        )
    else:
        system_prompt = (
            f"Extract a debt/loan from the message. Today is {today}.\n"
            "direction: 'owed_to_me' if someone owes the USER ('Ali owes me', 'I lent Ali'); "
            "'i_owe' if the USER owes ('I owe', 'I borrowed from', 'I took from').\n"
            "counterparty — the person/company name. amount — number (convert worded amounts). "
            f"currency — ISO code (one of {codes_str}; default {base_currency}). "
            "deadline — YYYY-MM-DD or null; convert relative deadlines ('by July 5', 'in a week') "
            "to a concrete date relative to today.\n"
            'Reply strictly JSON: {"direction":"owed_to_me|i_owe","counterparty":"<string>",'
            '"amount":<number>,"currency":"<ISO>","deadline":"<YYYY-MM-DD|null>"}'
        )

    raw = ""
    try:
        def _call():
            return client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
            )

        response = await asyncio.to_thread(_call)
        raw = response.choices[0].message.content
        logger.info("parse_debt -> %s", raw[:200])
        data = _extract_json(raw)
        direction = data.get("direction")
        if direction not in ("owed_to_me", "i_owe"):
            direction = "owed_to_me"
        counterparty = str(data.get("counterparty") or "").strip()[:120]
        amount = _to_float(data.get("amount"))
        currency = normalize_currency(data.get("currency"), base_currency)
        deadline = None
        dl = data.get("deadline")
        if dl and str(dl).lower() not in ("null", "none", ""):
            try:
                import datetime as _dt
                deadline = _dt.date.fromisoformat(str(dl)[:10]).isoformat()
            except ValueError:
                deadline = None
        return {"direction": direction, "counterparty": counterparty,
                "amount": amount, "currency": currency, "deadline": deadline}
    except Exception as e:
        logger.error("parse_debt_text error: %s | raw=%s", e, raw[:200])
        return {"direction": "owed_to_me", "counterparty": "", "amount": 0.0,
                "currency": base_currency, "deadline": None}


async def get_savings_tips(monthly_data: list, language: str, currency: str = DEFAULT_CURRENCY) -> str:
    if not monthly_data:
        return ""

    cur_word = currency_meta(currency)["symbol"]
    total = sum(_to_float(r.get("total_spent")) for r in monthly_data)
    lines = []
    for row in sorted(monthly_data, key=lambda x: _to_float(x.get("total_spent")), reverse=True):
        amt = _to_float(row.get("total_spent"))
        share = (amt / total * 100) if total else 0
        lines.append(
            f"- {row['category']}: {amt:,.0f} {cur_word} ({share:.0f}% трат, {row.get('num_transactions', 0)} операций)".replace(",", " ")
        )
    data_str = "\n".join(lines)

    if language == "ru":
        prompt = (
            f"Расходы пользователя за месяц (всего {total:,.0f} {cur_word}):\n{data_str}\n\n"
            "Дай ровно 3 коротких конкретных совета по экономии — с цифрами и реальными шагами, "
            "опираясь на самые крупные категории. Пиши по-дружески, без воды. "
            "Каждый совет — с новой строки, начни с эмодзи."
        ).replace(",", " ")
    else:
        prompt = (
            f"User's monthly expenses (total {total:,.0f} {cur_word}):\n{data_str}\n\n"
            "Give exactly 3 short, specific savings tips with numbers and concrete steps, "
            "focused on the largest categories. Friendly tone, no fluff. "
            "Each tip on a new line, starting with an emoji."
        ).replace(",", " ")

    try:
        def _call():
            return client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=700,
            )

        response = await asyncio.to_thread(_call)
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error("get_savings_tips error: %s", e)
        return ""
