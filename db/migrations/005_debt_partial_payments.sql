-- ════════════════════════════════════════════════════════════════════
-- Feature: partial debt repayments. Track how much of a debt is paid off.
-- Idempotent (safe to re-run). Additive — old code ignores the column.
-- ════════════════════════════════════════════════════════════════════

alter table public.debts
  add column if not exists paid_amount numeric not null default 0;

-- remaining = amount - paid_amount; a debt is 'settled' once paid_amount >= amount.
