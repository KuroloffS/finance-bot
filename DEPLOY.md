# 🚀 Деплой на Railway

Бот работает в режиме **long-polling** — нужен один постоянно работающий процесс
(тип `worker` в `Procfile`). Webhook не требуется. Конфиг уже готов:
`requirements.txt`, `runtime.txt` (Python 3.12), `Procfile` (`worker`), `railway.json`
(авто-перезапуск при сбое).

> ⚠️ **Важно:** у Telegram-бота может опрашивать обновления только ОДИН процесс.
> Перед запуском на Railway **останови локального бота** (иначе будет ошибка `409 Conflict`).

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

> **Не** задавай `WEBHOOK_URL` и `PORT` — без `WEBHOOK_URL` бот сам выберет polling.

### 5. Готово
Railway пересоберёт проект и запустит бота. Логи — во вкладке **Deployments → Logs**.
Должно появиться: `Application started`. Открой бота в Telegram → `/start`.

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

## Переход на webhook (опционально, позже)
Polling проще и работает сразу. Если захочешь webhook (эффективнее под нагрузкой):
1. В Railway включи публичный домен (Settings → Networking → Generate Domain).
2. Добавь переменную `WEBHOOK_URL` = выданный `https://...up.railway.app`.
3. Смени `Procfile` на `web: python main.py` и закоммить.
`main.py` сам переключится на webhook, когда увидит `WEBHOOK_URL`.
