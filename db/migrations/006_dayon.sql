-- ════════════════════════════════════════════════════════════════════
-- Dayon expansion: income transactions + calendar events + recurring
-- payments + tasks/folders. Idempotent (safe to re-run).
-- Additive: existing transactions default to type='expense', so the
-- budget/spend analytics and monthly_summary output are unchanged.
-- Run in Supabase → SQL Editor for a fresh database (after 001..005).
-- ════════════════════════════════════════════════════════════════════

-- ── 1. Income vs expense on transactions ──────────────────────────────
alter table public.transactions
  add column if not exists type text not null default 'expense';   -- 'expense' | 'income'

-- monthly_summary stays EXPENSE-ONLY so budget/analytics never count income.
create or replace view public.monthly_summary as
  select
    user_id,
    date_trunc('month', purchase_date::timestamptz) as month,
    category,
    sum(amount)   as total_spent,
    count(*)      as num_transactions,
    avg(amount)   as avg_transaction
  from public.transactions
  where coalesce(type, 'expense') = 'expense'
  group by user_id, date_trunc('month', purchase_date::timestamptz), category
  order by date_trunc('month', purchase_date::timestamptz) desc, sum(amount) desc;

-- ── 2. Calendar events ────────────────────────────────────────────────
create table if not exists public.events (
  id          bigserial primary key,
  user_id     bigint not null references public.users(telegram_id) on delete cascade,
  title       text   not null,
  event_date  date   not null,
  event_time  time,                                  -- nullable (all-day)
  note        text,
  emoji       text   default '📌',
  created_at  timestamptz default now()
);
create index if not exists events_user_date_idx on public.events(user_id, event_date);

-- ── 3. Recurring payments (subscriptions, rent, utilities, credits) ───
create table if not exists public.payments (
  id             bigserial primary key,
  user_id        bigint  not null references public.users(telegram_id) on delete cascade,
  name           text    not null,
  category       text    not null default 'Подписка',
  amount         numeric not null check (amount > 0),
  currency       text    not null default 'UZS',
  period         text    not null default 'monthly',  -- 'weekly' | 'monthly' | 'yearly'
  next_due_date  date    not null,
  last_paid_date date,                                 -- null = never paid yet
  status         text    not null default 'active',    -- 'active' | 'paused'
  note           text,
  created_at     timestamptz default now()
);
create index if not exists payments_user_idx on public.payments(user_id, status);

-- ── 4. Task folders + tasks ───────────────────────────────────────────
create table if not exists public.task_folders (
  id         bigserial primary key,
  user_id    bigint not null references public.users(telegram_id) on delete cascade,
  name       text   not null,
  emoji      text   default '📁',
  created_at timestamptz default now()
);
create index if not exists task_folders_user_idx on public.task_folders(user_id);

create table if not exists public.tasks (
  id           bigserial primary key,
  user_id      bigint not null references public.users(telegram_id) on delete cascade,
  title        text   not null,
  status       text   not null default 'active',  -- 'active' | 'done' | 'cancelled'
  priority     text,                               -- 'critical' | 'high' | 'medium' | 'low' | null
  due_date     date,
  folder_id    bigint references public.task_folders(id) on delete set null,
  tags         text[] not null default '{}',
  note         text,
  created_at   timestamptz default now(),
  completed_at timestamptz
);
create index if not exists tasks_user_idx on public.tasks(user_id, status);

-- ── 5. Secure-by-default: RLS on, no public policies → only the
--      service_role (bot) can touch these. Matches 003's lockdown.
alter table public.events       enable row level security;
alter table public.payments     enable row level security;
alter table public.task_folders enable row level security;
alter table public.tasks        enable row level security;
