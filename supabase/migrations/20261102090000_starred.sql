-- Starred notes (a visual marker set from the phone or the sidebar).
-- Safe to run more than once.
alter table public.cards add column if not exists starred boolean not null default false;
