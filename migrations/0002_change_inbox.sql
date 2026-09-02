-- 0002 — Change Inbox baseline storage.
--
-- Run after 0001. Idempotent; safe to re-run.
--
-- Why this is a table and not derived: playoff odds, positional ranks and the
-- standings AT A PAST MOMENT cannot be recomputed later from Sleeper, so "what
-- changed since my last sync" needs the historical record kept. It is small
-- (one row per material sync, a few hundred bytes each) and it is authoritative
-- Commissioner state, so it exports and restores with everything else.
--
-- snapshot_id, not taken_at, is the ordering key: two syncs inside the same
-- second are ordinary and a timestamp key silently overwrote the earlier one.

create table if not exists sync_snapshots (
  snapshot_id   bigserial primary key,
  league_slug   text not null,
  season        text not null,
  taken_at      timestamptz not null default now(),
  week          integer not null,
  payload_hash  text not null,
  payload       jsonb not null,
  reviewed_at   timestamptz
);

create index if not exists sync_snapshots_league_idx
  on sync_snapshots (league_slug, season, snapshot_id desc);

comment on column sync_snapshots.payload is
  'League state at this sync: standings, records, all-play, playoff odds, '
  'positional ranks, streaks, completed results, transaction ids, extremes.';
comment on column sync_snapshots.payload_hash is
  'Set by the application. A sync whose hash matches the latest row is not '
  'stored, so the previous row is always a genuinely different state.';
comment on column sync_snapshots.reviewed_at is
  'Stamped by "Mark all reviewed". Pins the Change Inbox baseline forward.';

-- ------------------------------------------------- same lockdown as 0001

do $$
begin
  execute 'alter table sync_snapshots enable row level security';
  execute 'alter table sync_snapshots force row level security';
  execute 'drop policy if exists commissioner_all on sync_snapshots';
  execute 'create policy commissioner_all on sync_snapshots for all to authenticated '
          'using (app_is_commissioner()) with check (app_is_commissioner())';
  execute 'revoke all on sync_snapshots from anon';
  execute 'grant select, insert, update, delete on sync_snapshots to authenticated';
  execute 'revoke all on sequence sync_snapshots_snapshot_id_seq from anon';
  execute 'grant usage, select on sequence sync_snapshots_snapshot_id_seq to authenticated';
end $$;
