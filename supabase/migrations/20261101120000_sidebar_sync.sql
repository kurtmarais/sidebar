-- Sidebar phone sync: categories (boards) and text cards.
-- Every row belongs to one Supabase Auth user; row-level security means a
-- signed-in user can only ever see or change their own rows.
-- Times are Unix epoch seconds written by the clients (newest edit wins);
-- server_updated_at is set by the database and is what clients sync from.

create table if not exists public.boards (
    uid               text primary key,
    user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name              text not null default '',
    position          double precision not null default 0,
    deleted           boolean not null default false,
    updated_at        double precision not null default extract(epoch from now()),
    server_updated_at timestamptz not null default now()
);

create table if not exists public.cards (
    uid               text primary key,
    user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
    board_uid         text not null default '',
    type              text not null default 'note'
                      check (type in ('note', 'checklist', 'code', 'links')),
    title             text not null default '',
    color             text not null default 'yellow',
    content           jsonb not null default '{}'::jsonb,
    position          double precision not null default 0,
    collapsed         boolean not null default false,
    priority          smallint not null default 0 check (priority between 0 and 3),
    due_date          text,
    remind_at         double precision,
    archived          boolean not null default false,
    archived_at       double precision,
    link_app          text not null default '',
    link_title        text not null default '',
    deleted           boolean not null default false,
    created_at        double precision not null default extract(epoch from now()),
    updated_at        double precision not null default extract(epoch from now()),
    server_updated_at timestamptz not null default now()
);

create index if not exists boards_user_sync on public.boards (user_id, server_updated_at);
create index if not exists cards_user_sync on public.cards (user_id, server_updated_at);

-- Newest edit wins: an upload carrying an older updated_at than the stored
-- row is ignored. Accepted writes get a fresh server_updated_at.
create or replace function public.sidebar_newest_wins()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        new.user_id := old.user_id;                 -- rows can never change owner
        if new.updated_at < old.updated_at then
            return old;
        end if;
    end if;
    new.server_updated_at := clock_timestamp();
    return new;
end;
$$;

drop trigger if exists boards_newest_wins on public.boards;
create trigger boards_newest_wins before insert or update on public.boards
    for each row execute function public.sidebar_newest_wins();

drop trigger if exists cards_newest_wins on public.cards;
create trigger cards_newest_wins before insert or update on public.cards
    for each row execute function public.sidebar_newest_wins();

alter table public.boards enable row level security;
alter table public.cards enable row level security;

drop policy if exists "own boards" on public.boards;
create policy "own boards" on public.boards for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "own cards" on public.cards;
create policy "own cards" on public.cards for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- Anonymous (signed-out) requests get nothing.
revoke all on public.boards, public.cards from anon;
grant select, insert, update, delete on public.boards, public.cards to authenticated;
