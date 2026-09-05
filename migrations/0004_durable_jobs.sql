-- 0004: jobs become a control plane rather than a status blob.
--
-- 0001 created `jobs` as a placeholder: kind, state, a `stages` jsonb the
-- worker rewrote in place, and nothing about who was executing it. That is
-- enough to show a spinner and not enough to survive a worker dying, which
-- is the case that actually costs something here: a publish that deployed
-- to production and then lost the process that would have recorded it.
--
-- This migration brings the Postgres table to the same shape the local
-- SQLite store now carries, so `PostgresJobRepository` can implement the
-- same contract as `SQLiteJobRepository` without anything above the
-- repository noticing which one it holds. Three ideas arrive with it:
--
--   * A **lease**. `lease_owner` plus `lease_expires_at` say who is
--     executing the job right now and until when. Every write is guarded by
--     the owner, so a worker that has been superseded cannot overwrite its
--     replacement's record. Reclaiming an expired lease is a single guarded
--     UPDATE, which is atomic here for the same reason it is in SQLite.
--   * An **idempotency key held only while the job is live**. A second Sync
--     click joins the running sync instead of starting a second one. The
--     key is released when the job ends, so a finished job never blocks the
--     next one. The partial unique index is the enforcement, not a
--     convention the application is trusted to follow.
--   * **Events instead of a stages blob.** `job_events` is append-only.
--     The stage list a browser renders is a fold over it, and the history
--     that fold discards is what a recovery reads to decide whether a
--     deploy went out.
--
-- `target_revision` is the other half of that. A publish binds to the exact
-- immutable revision its snapshot stage froze, and every later stage uses
-- that number rather than re-reading the directory, so a job cannot start
-- shipping r2 and finish shipping r3.
--
-- Idempotent: safe against a database that already has 0001 applied, and
-- safe to re-run. It preserves any rows 0001's table already holds.

-- ---------------------------------------------------------------- renames
-- 0001 called them id and kind. The rest of the system says job_id and
-- job_type, and one vocabulary is worth a rename.
do $$
begin
  if exists (select 1 from information_schema.columns
             where table_name = 'jobs' and column_name = 'id') then
    alter table jobs rename column id to job_id;
  end if;
  if exists (select 1 from information_schema.columns
             where table_name = 'jobs' and column_name = 'kind') then
    alter table jobs rename column kind to job_type;
  end if;
end $$;

create table if not exists jobs (
  job_id      uuid primary key default gen_random_uuid(),
  job_type    text not null,
  state       text not null default 'queued',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- What the job is about.
alter table jobs add column if not exists scope           text not null default 'global';
alter table jobs add column if not exists league_slug     text;
alter table jobs add column if not exists season          text;
alter table jobs add column if not exists issue_key       text;
alter table jobs add column if not exists mode            text;
alter table jobs add column if not exists request         jsonb not null default '{}'::jsonb;
alter table jobs add column if not exists result          jsonb not null default '{}'::jsonb;

-- The immutable target. Null until the snapshot stage freezes something.
alter table jobs add column if not exists target_revision integer;

-- Deduplication, live-only.
alter table jobs add column if not exists idempotency_key text;

-- Who is executing this, and until when.
alter table jobs add column if not exists lease_owner      text;
alter table jobs add column if not exists lease_expires_at timestamptz;
alter table jobs add column if not exists heartbeat_at     timestamptz;
alter table jobs add column if not exists attempts         integer not null default 0;
alter table jobs add column if not exists started_at       timestamptz;
alter table jobs add column if not exists ended_at         timestamptz;

-- `error` is the sentence a person reads; `error_code` is the stable slug a
-- screen or a test branches on without matching prose.
alter table jobs add column if not exists error      text;
alter table jobs add column if not exists error_code text;

-- Superseded by job_events. Dropped rather than left to rot into a second,
-- disagreeing account of what happened.
alter table jobs drop column if exists stages;

-- 0001 defaulted this to 'running', which was true when a row was only
-- ever written by the thread already doing the work. A job is now created
-- before anything claims it.
alter table jobs alter column state set default 'queued';

comment on column jobs.state is
  'queued | running | succeeded | failed | lost. "lost" means the worker '
  'stopped renewing its lease: not that the work failed, but that nobody '
  'can say whether it finished.';
comment on column jobs.target_revision is
  'The immutable revision this job is bound to. Once set it never changes, '
  'so a publish cannot switch targets between snapshot and deploy.';
comment on column jobs.idempotency_key is
  'Held only while the job is live and released when it ends, so a running '
  'job is joined rather than duplicated and a finished one blocks nothing.';

-- Partial, so released keys on finished jobs cannot collide.
create unique index if not exists idx_jobs_idempotency
  on jobs (idempotency_key) where idempotency_key is not null;
create index if not exists idx_jobs_recent on jobs (job_type, created_at desc);
create index if not exists idx_jobs_target
  on jobs (league_slug, season, issue_key, created_at desc);
-- The reaper's query, and the only one that runs on a timer.
create index if not exists idx_jobs_lease on jobs (state, lease_expires_at);

drop index if exists jobs_recent_idx;

-- ------------------------------------------------------------ job_events
-- Append-only. A stage is not a field that gets rewritten, it is a sequence
-- of facts about a stage, and the difference is what makes "what had
-- already happened when the process died" an answerable question.
create table if not exists job_events (
  event_id   bigint generated always as identity primary key,
  job_id     uuid not null references jobs(job_id) on delete cascade,
  seq        integer not null,
  stage_key  text not null,
  stage_name text not null default '',
  status     text not null,          -- pending | running | ok | failed | skipped
  detail     text not null default '',
  at         timestamptz not null default now()
);
create unique index if not exists idx_job_events_seq on job_events (job_id, seq);

comment on table job_events is
  'Append-only stage history. The stage list the Desk renders is a fold '
  'over these rows; the fold is last-write-wins per stage_key and the order '
  'is the order the stages were declared when the job was created.';

-- ------------------------------------------------- lock the new table down
-- Same policy as every other table in 0001: RLS forced, anon gets nothing,
-- and an authenticated user reads only if they are on the allowlist.
do $$
declare t text;
begin
  foreach t in array array['jobs', 'job_events'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists commissioner_all on %I', t);
    execute format(
      'create policy commissioner_all on %I for all to authenticated '
      'using (app_is_commissioner()) with check (app_is_commissioner())', t);
    execute format('revoke all on %I from anon', t);
    execute format('grant select, insert, update, delete on %I to authenticated', t);
  end loop;
end $$;

-- The identity sequence behind job_events must not be reachable by anon.
do $$
declare s text;
begin
  for s in select sequence_name from information_schema.sequences
           where sequence_schema = 'public'
  loop
    execute format('revoke all on sequence %I from anon', s);
    execute format('grant usage, select on sequence %I to authenticated', s);
  end loop;
end $$;
