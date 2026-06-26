-- ════════════════════════════════════════════════════════════════════
-- Security: lock users + transactions down to the service_role.
-- Idempotent (safe to re-run). Already applied to the production project.
-- ════════════════════════════════════════════════════════════════════
--
-- These tables historically had PERMISSIVE policies for the `public` role
-- (cmd ALL, using true), which let the anon/public key read & write every
-- user's data. The bot and Mini App backend use the service_role key, which
-- bypasses RLS entirely, so dropping these public policies changes nothing for
-- the app but removes the exposure — matching the secure-by-default stance of
-- goals / goal_contributions / notifications_log (RLS on, no policies).

drop policy if exists s_users               on public.users;
drop policy if exists service_users         on public.users;
drop policy if exists s_transactions        on public.transactions;
drop policy if exists service_transactions  on public.transactions;

-- Keep RLS enabled (no policies → only the service_role can touch these).
alter table public.users        enable row level security;
alter table public.transactions enable row level security;
