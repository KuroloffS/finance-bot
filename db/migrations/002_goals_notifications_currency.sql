-- ════════════════════════════════════════════════════════════════════
-- Feature migration: savings goals + smart notifications + multi-currency
-- Idempotent (safe to re-run). Already applied to the production project.
-- Run in Supabase → SQL Editor if setting up a fresh database.
-- ════════════════════════════════════════════════════════════════════

-- ── Multi-currency ──────────────────────────────────────────────────
-- transactions.amount stays in the user's BASE currency (so the
-- monthly_summary view and all budget/analytics math are unchanged).
-- The original entry amount/currency are kept alongside for transparency.
alter table public.transactions
  add column if not exists original_amount   numeric,
  add column if not exists original_currency text;

-- ── Smart notifications: per-user preferences (merged with code defaults) ──
alter table public.users
  add column if not exists notify_settings jsonb not null default '{}'::jsonb;

-- ── Savings goals ───────────────────────────────────────────────────
create table if not exists public.goals (
  id            bigserial primary key,
  user_id       bigint not null references public.users(telegram_id) on delete cascade,
  title         text   not null,
  emoji         text   not null default '🎯',
  target_amount numeric not null check (target_amount > 0),
  saved_amount  numeric not null default 0,
  currency      text   not null default 'UZS',
  deadline      date,
  status        text   not null default 'active',   -- active | done | archived
  created_at    timestamptz default now()
);
create index if not exists goals_user_idx on public.goals(user_id, status);

-- Ledger of contributions (kept OUT of transactions so the spend view is
-- never polluted by savings).
create table if not exists public.goal_contributions (
  id         bigserial primary key,
  goal_id    bigint not null references public.goals(id) on delete cascade,
  user_id    bigint not null references public.users(telegram_id) on delete cascade,
  amount     numeric not null,
  note       text,
  created_at timestamptz default now()
);
create index if not exists goal_contrib_goal_idx on public.goal_contributions(goal_id);

-- ── Notification dedup log (e.g. "budget_80:2026-06" sent once per month) ──
create table if not exists public.notifications_log (
  id         bigserial primary key,
  user_id    bigint not null references public.users(telegram_id) on delete cascade,
  type       text not null,
  dedup_key  text not null,
  created_at timestamptz default now(),
  unique (user_id, dedup_key)
);
create index if not exists notif_log_user_idx on public.notifications_log(user_id);

-- Secure-by-default: RLS on, no public policies → only the service_role (bot) can touch these.
alter table public.goals               enable row level security;
alter table public.goal_contributions  enable row level security;
alter table public.notifications_log   enable row level security;
