# 🚀 Деплой на Railway

Один процесс делает всё: Telegram-бот (long-polling) **+** веб-сервер (FastAPI/uvicorn),
который отдаёт **Mini App** (тёмное приложение с аналитикой) по HTTPS. Тип процесса — `web`
(`Procfile`), он слушает `$PORT`, который Railway выдаёт автоматически. Конфиг готов:
`requirements.txt`, `runtime.txt` (Python 3.12), `Procfile` (`web`), `railway.json`.

> ⚠️ **Важно:** опрашивать бота может только ОДИН процесс. Перед запуском на Railway
> **останови локального бота** (иначе `409 Conflict`).

## ✨ Возможности (обновление)
Бот умеет:
- 🎯 **Цели накопления** — `/goals` в боте и вкладка целей в Mini App: круговой прогресс,
  пополнения, дедлайны и подсказка «сколько откладывать в день».
- 🔔 **Умные уведомления** — пороги бюджета 80%/100% и крупные траты (в реальном времени),
  итоги дня, итоги недели, напоминания о целях. Тумблеры — `/settings` или Профиль в Mini App.
- 💱 **Мультивалютность** — `/currency` или Профиль: UZS, USD, EUR, RUB, KZT, TRY, GBP, AED.
  Бюджет и аналитика считаются в основной валюте, а вводить траты можно в любой
  («такси 12 долларов») — бот сам переведёт по актуальному курсу (бесплатный API + кэш).

### 🗄 Миграция БД (нужна для новых функций)
Схема расширяется аддитивно (новые таблицы `goals`, `goal_contributions`,
`notifications_log` + nullable-колонки). **На текущем проекте уже применена.** Для новой/чистой
базы выполни SQL по порядку в Supabase → SQL Editor: сначала
`db/migrations/001_initial_schema.sql` (базовые таблицы `users`, `transactions`
и view `monthly_summary`), затем `db/migrations/002_goals_notifications_currency.sql`.
Новых переменных окружения не требуется — курсы валют берутся с бесключевого API.

## 📱 Чтобы заработал Mini App (обязательно)
1. В сервисе Railway → **Settings → Networking → Generate Domain** — получишь адрес вида
   `https://finance-bot-production.up.railway.app`.
2. Railway сам прокинет переменную `RAILWAY_PUBLIC_DOMAIN`, и бот возьмёт её автоматически —
   **либо** задай переменную `WEBAPP_URL` = этот `https://…` адрес вручную.
3. После редеплоя бот выставит кнопку-меню «💼 Финансы» (рядом с полем ввода) и кнопку
   «📱 Открыть приложение» в `/start` и `/app`. Tap → откроется тёмное приложение.

> Telegram Mini Apps работают только по HTTPS — домен Railway уже https, всё ок.

---

## Вариант A — через GitHub (рекомендуется, авто-деплой при `git push`)

### 1. Создай репозиторий на GitHub
Зайди на https://github.com/new → создай пустой репозиторий (например `finance-bot`),
**без** README/.gitignore (они уже есть локально).

### 2. Запушь код
В этой сессии выполни (подставь свой URL репозитория):
```
! git remote add origin https://github.com/ТВОЙ_ЛОГИН/finance-bot.git
! git push -u origin master
```
> `.env` не попадёт в репозиторий — он в `.gitignore`. Уйдёт только `.env.example`.

### 3. Создай проект на Railway
1. Зайди на https://railway.app → **New Project** → **Deploy from GitHub repo**.
2. Авторизуй Railway в GitHub и выбери репозиторий `finance-bot`.
3. Railway сам определит Python (nixpacks), поставит зависимости и запустит `worker`.

### 4. Задай переменные окружения
В проекте → вкладка **Variables** → добавь (значения возьми из своего `.env`):

| Переменная | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `GROQ_API_KEY` | ключ Groq |
| `SUPABASE_URL` | `https://ldttcomjzlyyeolblxry.supabase.co` |
| `SUPABASE_SERVICE_KEY` | service_role ключ Supabase |
| `DEFAULT_CURRENCY` | `UZS` |
| `DEFAULT_MONTHLY_BUDGET` | `5000000` |
| `DEFAULT_LANGUAGE` | `ru` |
| `WEBAPP_URL` | (опц.) `https://…up.railway.app` — для Mini App; можно не задавать, если домен сгенерирован (см. раздел 📱 выше) |

> `PORT` задаёт Railway сам — не трогай. `RAILWAY_PUBLIC_DOMAIN` подставляется
> автоматически после генерации домена, и бот возьмёт адрес Mini App из неё.

### 5. Готово
Railway пересоберёт проект и запустит бота + веб-сервер. Логи — во вкладке **Deployments → Logs**.
Должно появиться: `Bot polling + web server on port …` и `Menu button → Mini App at https://…`.
Открой бота в Telegram → `/start` → нажми «📱 Открыть приложение».

При следующем `git push` Railway задеплоит обновление автоматически.

---

## Вариант B — через Railway CLI

```
! npm i -g @railway/cli
! railway login          # откроется браузер для входа
! railway init           # создать проект
! railway up             # загрузить и задеплоить текущую папку
```
Переменные можно задать командой:
```
! railway variables --set "TELEGRAM_BOT_TOKEN=..." --set "GROQ_API_KEY=..." --set "SUPABASE_URL=https://ldttcomjzlyyeolblxry.supabase.co" --set "SUPABASE_SERVICE_KEY=..." --set "DEFAULT_CURRENCY=UZS" --set "DEFAULT_MONTHLY_BUDGET=5000000" --set "DEFAULT_LANGUAGE=ru"
```

---

## Как это устроено
- `main.py` поднимает в одном процессе: бот (long-polling) + FastAPI/uvicorn (порт `$PORT`).
- Веб-сервер отдаёт Mini App (`web/static/index.html`) и JSON-API (`/api/*`).
- Каждый запрос `/api/*` проверяет подпись Telegram `initData` (HMAC по токену) — личность
  берётся только из проверенных данных, чужие данные недоступны.
- Бот и приложение пишут в одну базу Supabase — данные всегда синхронны.
