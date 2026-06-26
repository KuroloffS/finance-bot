-- ════════════════════════════════════════════════════════════════════
-- Baseline schema: users + transactions + monthly_summary view
-- Idempotent (safe to re-run). Run this FIRST, then 002.
-- Run in Supabase → SQL Editor when setting up a fresh database.
-- ════════════════════════════════════════════════════════════════════

-- ── Users ───────────────────────────────────────────────────────────
-- telegram_id is the natural key used everywhere as user_id (all FKs point here).
create table if not exists public.users (
  id             bigserial primary key,
  telegram_id    bigint not null unique,
  username       text,
  first_name     text,
  language       text    default 'ru',
  monthly_budget numeric default 5000000,
  currency       text    default 'UZS',
  created_at     timestamptz default now()
);

-- ── Transactions ────────────────────────────────────────────────────
-- amount is stored in the user's BASE currency (see currency_service).
create table if not exists public.transactions (
  id            bigserial primary key,
  user_id       bigint not null references public.users(telegram_id) on delete cascade,
  amount        numeric not null,
  category      text   not null,
  description   text,
  merchant      text,
  ai_advice     text,
  input_type    text   default 'text',
  purchase_date date   default current_date,
  created_at    timestamptz default now()
);
create index if not exists transactions_user_date_idx
  on public.transactions(user_id, purchase_date);

-- ── Monthly category summary (read by get_monthly_summary*) ──────────
-- One row per (user, month, category) with totals; month is the first of the month.
create or replace view public.monthly_summary as
  select
    user_id,
    date_trunc('month', purchase_date::timestamptz) as month,
    category,
    sum(amount)   as total_spent,
    count(*)      as num_transactions,
    avg(amount)   as avg_transaction
  from public.transactions
  group by user_id, date_trunc('month', purchase_date::timestamptz), category
  order by date_trunc('month', purchase_date::timestamptz) desc, sum(amount) desc;

-- Secure-by-default: RLS on, no public policies → only the service_role (bot) can touch these.
alter table public.users        enable row level security;
alter table public.transactions enable row level security;
