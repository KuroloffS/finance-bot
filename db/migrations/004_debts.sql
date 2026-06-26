-- ════════════════════════════════════════════════════════════════════
-- Feature migration: debts / loans (кто кому должен, сумма, срок)
-- Idempotent (safe to re-run). Already applied to the production project.
-- ════════════════════════════════════════════════════════════════════

create table if not exists public.debts (
  id           bigserial primary key,
  user_id      bigint  not null references public.users(telegram_id) on delete cascade,
  direction    text    not null,                       -- 'owed_to_me' (мне должны) | 'i_owe' (я должен)
  counterparty text    not null,                        -- имя человека/компании
  amount       numeric not null check (amount > 0),
  currency     text    not null default 'UZS',
  due_date     date,                                    -- срок возврата (nullable)
  status       text    not null default 'open',         -- 'open' | 'settled'
  note         text,
  created_at   timestamptz default now()
);
create index if not exists debts_user_idx on public.debts(user_id, status);

-- Secure-by-default: RLS on, no public policies → only the service_role (bot) can touch it.
alter table public.debts enable row level security;
