-- Optional time on a card's due date, "HH:MM" (24-hour, local time). Safe to run more than once.
alter table public.cards add column if not exists due_time text
    check (due_time is null or due_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$');
