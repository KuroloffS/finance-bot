TEXTS = {
    "ru": {
        "welcome": (
            "👋 <b>Привет, {name}!</b>\n"
            "Я твой личный финансовый советник 💸\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Просто расскажи о трате — любым способом:\n"
            "⌨️ <b>Текстом</b> — «такси 25 000»\n"
            "🎤 <b>Голосом</b> — наговори покупку\n"
            "📸 <b>Фото</b> — пришли чек\n\n"
            "Я запишу, разложу по категориям и дам совет 🤝\n"
            "🎯 А ещё помогу копить на цели и подскажу, когда пора притормозить.\n\n"
            "👇 А кнопки внизу — для всего остального."
        ),
        "help_text": (
            "ℹ️ <b>Как пользоваться</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "⌨️ <b>Текст:</b> «потратил 50 000 на такси»\n"
            "🎤 <b>Голос:</b> запиши голосовое о трате\n"
            "📸 <b>Фото:</b> сфотографируй чек\n"
            "🎧 <b>Аудио:</b> пришли аудиофайл\n"
            "💱 Можно в любой валюте: «такси 12 долларов» — переведу сам.\n\n"
            "<b>Команды</b>\n"
            "💰 /budget [сумма] — установить бюджет\n"
            "🎯 /goals — цели накопления\n"
            "📊 /report — отчёт за месяц\n"
            "📋 /history — последние 10 трат\n"
            "💡 /tips — советы по экономии\n"
            "💱 /currency — валюта\n"
            "🔔 /settings — уведомления\n"
            "↩️ /undo — удалить последнюю трату\n"
            "🗑 /reset — удалить все траты\n"
            "🌐 /lang ru|en — сменить язык"
        ),
        "budget_set": "✅ <b>Бюджет обновлён</b>\n💰 {amount} в месяц",
        "budget_invalid": "❌ Укажи сумму числом.\nНапример: <code>/budget 5000000</code>",
        "voice_transcribed": "🎤 <i>Распознал:</i> «{text}»",
        "photo_processing": "📸 Изучаю чек…",
        "audio_processing": "🎧 Слушаю аудио…",
        "parse_error": (
            "🤔 Не понял трату. Напиши конкретнее с суммой,\n"
            "например: «кофе 15 000» или «такси 25000 сум»"
        ),
        "empty_history": "📭 Трат пока нет.\nРасскажи мне о первой покупке 🙂",
        "empty_report": "📭 В этом месяце трат ещё нет.",
        "lang_changed": "✅ Язык изменён на русский 🇷🇺",
        "lang_usage": "🌐 Используй: <code>/lang ru</code> или <code>/lang en</code>",
        "tips_loading": "💡 Анализирую твои траты…",
        "no_data_tips": "📭 Пока нет данных за месяц для анализа.\nДобавь несколько трат — и я дам советы 🙂",
        "error_generic": "❌ Что-то пошло не так. Попробуй ещё раз.",
        "history_hint": "Нажми 🗑, чтобы удалить трату",
        "undo_done": "🗑 <b>Удалено</b>\n{emoji} {category} — {amount}",
        "undo_empty": "📭 Нечего удалять — трат пока нет.",
        "delete_done": "🗑 Трата удалена.",
        "delete_failed": "❌ Не получилось удалить — возможно, уже удалена.",
        "reset_confirm": (
            "⚠️ <b>Удалить все траты?</b>\n\n"
            "Будет удалено: <b>{count}</b>.\n"
            "Это действие необратимо."
        ),
        "reset_empty": "📭 Трат нет — удалять нечего.",
        "reset_done": "🗑 <b>Готово.</b> Удалено: {count}.",
        "reset_cancelled": "✅ Отменено. Все данные на месте.",
        "menu_hint": "Готово! Пользуйся кнопками внизу 👇",
        "open_app_hint": "📱 <b>Финансовое приложение</b>\nКрасивая аналитика, графики и быстрое добавление трат — всё в одном экране.",
        "app_unavailable": "📱 Приложение ещё не настроено (нужен публичный адрес). Пользуйся кнопками меню.",
        "budget_view": (
            "💰 <b>Месячный бюджет</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Текущий: <b>{amount}</b>\n"
            "{gauge}\n\n"
            "Выбери новый или нажми «Своя сумма»:"
        ),
        "budget_custom_prompt": "✏️ Введи сумму бюджета числом.\nНапример: <code>/budget 6000000</code>",
        "more_menu": "⚙️ <b>Ещё</b>\n━━━━━━━━━━━━━━━━━━━\n\nВыбери действие:",
        # ── Savings goals ──
        "goals_hint": "Нажми на цель, чтобы пополнить или изменить",
        "goal_new_prompt": (
            "🎯 <b>Новая цель</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Отправь одним сообщением:\n"
            "<b>Название · сумма · срок</b>\n\n"
            "Примеры:\n"
            "<code>Отпуск · 10 000 000 · 2026-12-31</code>\n"
            "<code>Новый ноутбук · 12000000</code>\n\n"
            "Срок (ГГГГ-ММ-ДД) можно не указывать."
        ),
        "goal_create_invalid": (
            "🤔 Не понял цель.\nФормат: <b>Название · сумма · срок</b>\n"
            "Например: <code>Машина · 50000000 · 2027-01-01</code>"
        ),
        "goal_created": "✅ <b>Цель создана!</b>\nЯ помогу тебе накопить 💪",
        "goal_contribute_prompt": "💰 Сколько отложить в «{title}»?\nОтправь сумму числом (в {currency}).",
        "goal_contributed": "✅ <b>Отложено {amount}</b> → «{title}»\n<b>{pct}%</b>",
        "goal_contribute_invalid": "❌ Укажи сумму числом, например <code>200000</code>.",
        "goal_done_celebrate": "🏆 <b>Цель достигнута!</b>\n«{title}» — {amount}\nТы молодец! 🎉",
        "goal_deleted": "🗑 Цель удалена.",
        "goal_not_found": "📭 Цель не найдена.",
        "goal_detail_hint": "💰 Пополнить · ✏️ Изменить срок · 🗑 Удалить",
        "goal_edit_deadline_prompt": "📅 Отправь новый срок (ГГГГ-ММ-ДД) или <code>-</code>, чтобы убрать.",
        "goal_deadline_set": "✅ Срок обновлён.",
        "goal_deadline_cleared": "✅ Срок убран.",
        # ── Currency ──
        "currency_prompt": (
            "💱 <b>Валюта</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
            "Текущая: <b>{currency}</b>\n"
            "Выбери основную валюту — в ней считаются бюджет и аналитика:"
        ),
        "currency_changed": (
            "✅ Основная валюта: <b>{currency}</b>\n"
            "💡 Прежние траты остаются как есть. Новые можно вводить в любой валюте — переведу автоматически."
        ),
        "currency_usage": "💱 Используй: <code>/currency USD</code>\nДоступно: {codes}",
        # ── Notification settings ──
        "settings_title": (
            "🔔 <b>Уведомления</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
            "Выбери, о чём напоминать. Нажми, чтобы включить или выключить:"
        ),
        "settings_saved": "✅ Сохранено.",
    },
    "en": {
        "welcome": (
            "👋 <b>Hi, {name}!</b>\n"
            "I'm your personal financial advisor 💸\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Just tell me about a purchase — any way:\n"
            "⌨️ <b>Text</b> — 'taxi 25000'\n"
            "🎤 <b>Voice</b> — say the purchase\n"
            "📸 <b>Photo</b> — send a receipt\n\n"
            "I'll log it, categorize it and give advice 🤝\n"
            "🎯 I'll also help you save toward goals and nudge you when to ease off.\n\n"
            "👇 The buttons below do everything else."
        ),
        "help_text": (
            "ℹ️ <b>How to use</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "⌨️ <b>Text:</b> 'spent 50000 on taxi'\n"
            "🎤 <b>Voice:</b> record a voice note\n"
            "📸 <b>Photo:</b> snap a receipt\n"
            "🎧 <b>Audio:</b> send an audio file\n"
            "💱 Any currency works: 'taxi 12 dollars' — I'll convert it.\n\n"
            "<b>Commands</b>\n"
            "💰 /budget [amount] — set budget\n"
            "🎯 /goals — savings goals\n"
            "📊 /report — monthly report\n"
            "📋 /history — last 10 transactions\n"
            "💡 /tips — savings tips\n"
            "💱 /currency — currency\n"
            "🔔 /settings — notifications\n"
            "↩️ /undo — delete last transaction\n"
            "🗑 /reset — delete all transactions\n"
            "🌐 /lang ru|en — change language"
        ),
        "budget_set": "✅ <b>Budget updated</b>\n💰 {amount} / month",
        "budget_invalid": "❌ Please provide a number.\nExample: <code>/budget 5000000</code>",
        "voice_transcribed": "🎤 <i>Heard:</i> “{text}”",
        "photo_processing": "📸 Reading the receipt…",
        "audio_processing": "🎧 Listening to the audio…",
        "parse_error": (
            "🤔 Didn't catch the purchase. Be more specific with an amount,\n"
            "e.g.: 'coffee 15000' or 'taxi 25000 sum'"
        ),
        "empty_history": "📭 No transactions yet.\nTell me about your first purchase 🙂",
        "empty_report": "📭 No transactions this month yet.",
        "lang_changed": "✅ Language changed to English 🇬🇧",
        "lang_usage": "🌐 Use: <code>/lang ru</code> or <code>/lang en</code>",
        "tips_loading": "💡 Analyzing your expenses…",
        "no_data_tips": "📭 No data this month yet.\nAdd a few purchases and I'll give tips 🙂",
        "error_generic": "❌ Something went wrong. Please try again.",
        "history_hint": "Tap 🗑 to delete a transaction",
        "undo_done": "🗑 <b>Deleted</b>\n{emoji} {category} — {amount}",
        "undo_empty": "📭 Nothing to delete — no transactions yet.",
        "delete_done": "🗑 Transaction deleted.",
        "delete_failed": "❌ Couldn't delete — it may already be gone.",
        "reset_confirm": (
            "⚠️ <b>Delete all transactions?</b>\n\n"
            "Will delete: <b>{count}</b>.\n"
            "This cannot be undone."
        ),
        "reset_empty": "📭 No transactions to delete.",
        "reset_done": "🗑 <b>Done.</b> Deleted: {count}.",
        "reset_cancelled": "✅ Cancelled. All data is intact.",
        "menu_hint": "Done! Use the buttons below 👇",
        "open_app_hint": "📱 <b>Finance app</b>\nBeautiful analytics, charts and quick add — all on one screen.",
        "app_unavailable": "📱 The app isn't configured yet (needs a public URL). Use the menu buttons.",
        "budget_view": (
            "💰 <b>Monthly budget</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Current: <b>{amount}</b>\n"
            "{gauge}\n\n"
            "Pick a new one or tap “Custom”:"
        ),
        "budget_custom_prompt": "✏️ Send the budget amount as a number.\nExample: <code>/budget 6000000</code>",
        "more_menu": "⚙️ <b>More</b>\n━━━━━━━━━━━━━━━━━━━\n\nChoose an action:",
        # ── Savings goals ──
        "goals_hint": "Tap a goal to top it up or edit it",
        "goal_new_prompt": (
            "🎯 <b>New goal</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Send it in one message:\n"
            "<b>Title · amount · deadline</b>\n\n"
            "Examples:\n"
            "<code>Vacation · 10000000 · 2026-12-31</code>\n"
            "<code>New laptop · 12000000</code>\n\n"
            "The deadline (YYYY-MM-DD) is optional."
        ),
        "goal_create_invalid": (
            "🤔 Couldn't read that goal.\nFormat: <b>Title · amount · deadline</b>\n"
            "e.g.: <code>Car · 50000000 · 2027-01-01</code>"
        ),
        "goal_created": "✅ <b>Goal created!</b>\nI'll help you save 💪",
        "goal_contribute_prompt": "💰 How much to put toward “{title}”?\nSend a number (in {currency}).",
        "goal_contributed": "✅ <b>Added {amount}</b> → “{title}”\n<b>{pct}%</b>",
        "goal_contribute_invalid": "❌ Send an amount as a number, e.g. <code>200000</code>.",
        "goal_done_celebrate": "🏆 <b>Goal reached!</b>\n“{title}” — {amount}\nWell done! 🎉",
        "goal_deleted": "🗑 Goal deleted.",
        "goal_not_found": "📭 Goal not found.",
        "goal_detail_hint": "💰 Top up · ✏️ Edit deadline · 🗑 Delete",
        "goal_edit_deadline_prompt": "📅 Send a new deadline (YYYY-MM-DD), or <code>-</code> to clear it.",
        "goal_deadline_set": "✅ Deadline updated.",
        "goal_deadline_cleared": "✅ Deadline cleared.",
        # ── Currency ──
        "currency_prompt": (
            "💱 <b>Currency</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
            "Current: <b>{currency}</b>\n"
            "Pick your base currency — budget and analytics are calculated in it:"
        ),
        "currency_changed": (
            "✅ Base currency: <b>{currency}</b>\n"
            "💡 Existing transactions stay as-is. New ones can be entered in any currency — I'll convert."
        ),
        "currency_usage": "💱 Use: <code>/currency USD</code>\nAvailable: {codes}",
        # ── Notification settings ──
        "settings_title": (
            "🔔 <b>Notifications</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose what to be reminded about. Tap to turn on or off:"
        ),
        "settings_saved": "✅ Saved.",
    },
}


def t(key: str, lang: str, **kwargs) -> str:
    text = TEXTS.get(lang, TEXTS["ru"]).get(key, TEXTS["ru"].get(key, key))
    return text.format(**kwargs) if kwargs else text
