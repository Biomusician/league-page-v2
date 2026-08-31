-- League-Page — Commissioner authoring state (Supabase / Postgres)
-- Migration 0001. Safe to re-run: every statement is idempotent.
--
-- HOW TO APPLY (no credential needs to leave your machine):
--   Supabase dashboard -> SQL Editor -> New query -> paste this file -> Run.
--
-- WHAT LIVES HERE
--   Only authoritative mutable Commissioner state: the words you write, the
--   decisions you make, and the durable job records the browser polls.
--   Recon (2026-08-31) measured that at ~250 rows plus ~57 KB of prose.
--   Sleeper/archive cache (players, rosters, matchups, drafts, transactions,
--   archive text) is NOT here: any sync rebuilds it, and copying 12,700 rows
--   of cache into Postgres would buy nothing.
--
-- SECURITY MODEL
--   Row Level Security is enabled AND forced on every table, with no
--   permissive default. The only policy grants access to an authenticated
--   user whose JWT email appears in app_commissioners. "Any authenticated
--   user" is deliberately NOT sufficient: a valid Supabase account that is
--   not a listed Commissioner reads nothing and writes nothing.
--   The anon (publishable-key) role is granted nothing at all.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- identity

create table if not exists app_commissioners (
  email       text primary key,
  note        text,
  created_at  timestamptz not null default now()
);

comment on table app_commissioners is
  'Server-side allowlist. Membership here is what authorizes access; being a
   valid Supabase user is not. Keep this in step with
   LEAGUEPAGE_COMMISSIONER_EMAILS.';

-- SECURITY DEFINER so the policy can read the allowlist without the caller
-- needing rights on it, and so the table can stay locked to the caller.
create or replace function app_is_commissioner()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from app_commissioners c
    where lower(c.email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

comment on function app_is_commissioner is
  'True only for an authenticated user whose JWT email is on the allowlist.';

-- ------------------------------------------------------------ editorial

create table if not exists issues (
  league_slug   text not null,
  season        text not null,
  issue_key     text not null,
  status        text not null default 'draft',
  published_at  timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  primary key (league_slug, season, issue_key)
);

create table if not exists issue_modules (
  league_slug   text not null,
  season        text not null,
  issue_key     text not null,
  module_key    text not null,
  position      integer,
  included      boolean not null default true,
  custom_title  text,
  approved      boolean not null default false,
  updated_at    timestamptz not null default now(),
  primary key (league_slug, season, issue_key, module_key)
);

-- The crux of the migration: prose is filesystem-authoritative today
-- (editorial/**/*.md). Here it becomes a row, so a serverless request can
-- read and write it. `version` powers optimistic concurrency: a save that
-- carries a stale version is refused rather than silently clobbering a
-- newer edit made in another tab or arriving from email.
create table if not exists sections (
  league_slug   text not null,
  season        text not null,
  issue_key     text not null,
  section       text not null,
  content       text not null default '',
  state         text not null default 'generated',
  version       integer not null default 1,
  updated_at    timestamptz not null default now(),
  primary key (league_slug, season, issue_key, section)
);

comment on column sections.state is
  'generated | commissioner-edited. Never downgrade commissioner-edited.';

create table if not exists prose_revisions (
  id            bigserial primary key,
  league_slug   text not null,
  season        text not null,
  issue_key     text not null,
  section       text not null,
  source        text not null,
  prior_text    text,
  created_at    timestamptz not null default now()
);

comment on column prose_revisions.source is
  'local-web | remote-web | email-proposal | claude-proposal | restore';

create index if not exists prose_revisions_section_idx
  on prose_revisions (league_slug, season, issue_key, section, created_at desc);

create table if not exists issue_revision_requests (
  id            bigserial primary key,
  league_slug   text not null,
  season        text not null,
  issue_key     text not null,
  section       text not null,
  note          text,
  status        text not null default 'open',
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz
);

-- --------------------------------------------------------- decisions

create table if not exists team_names (
  league_slug   text not null,
  roster_id     integer not null,
  public_name   text,
  confirmed_at  timestamptz not null default now(),
  primary key (league_slug, roster_id)
);

create table if not exists story_decisions (
  league_slug   text not null,
  season        text not null,
  workflow      text not null,
  candidate_id  text not null,
  decision      text not null,
  note          text,
  route         text,
  decided_at    timestamptz not null default now(),
  primary key (league_slug, season, workflow, candidate_id)
);

create table if not exists award_decisions (
  league_slug   text not null,
  season        text not null,
  workflow      text not null,
  award_key     text not null,
  decision      text not null,
  winner        text,
  note          text,
  decided_at    timestamptz not null default now(),
  primary key (league_slug, season, workflow, award_key)
);

create table if not exists matchup_state (
  league_slug        text not null,
  season             text not null,
  week               integer not null,
  matchup_slug       text not null,
  selected_angle_id  text,
  custom_angle       text,
  angle_note         text,
  prominence_override text,
  status             text,
  updated_at         timestamptz not null default now(),
  primary key (league_slug, season, week, matchup_slug)
);

create table if not exists power_rankings (
  league_slug   text not null,
  season        text not null,
  label         text not null,
  roster_id     integer not null,
  rank          integer,
  tier          integer,
  note          text,
  updated_at    timestamptz not null default now(),
  primary key (league_slug, season, label, roster_id)
);

create table if not exists takes (
  take_id       bigserial primary key,
  league_slug   text not null,
  season        text not null,
  week          integer,
  source        text,
  subject       text,
  quote         text,
  confidence    text,
  status        text,
  created_at    timestamptz not null default now()
);

create table if not exists editorial_usage (
  usage_id      bigserial primary key,
  league_slug   text not null,
  season        text not null,
  week          integer,
  matchup_slug  text,
  kind          text,
  value         text,
  note          text,
  used_at       timestamptz not null default now()
);

create table if not exists bit_usage (
  usage_id      bigserial primary key,
  manager_key   text not null,
  bit           text not null,
  league_slug   text,
  season        text,
  week          integer,
  note          text,
  used_at       timestamptz not null default now()
);

-- Editorial key/value: transaction before/after context, analytics
-- snapshots, deploy state. Sync bookkeeping stays local.
create table if not exists editorial_meta (
  key           text primary key,
  value         text,
  updated_at    timestamptz not null default now()
);

-- ------------------------------------------------------------- jobs
-- Durable replacement for the daemon-thread job globals, which cannot
-- survive a serverless runtime. The browser polls these rows.

create table if not exists jobs (
  id            uuid primary key default gen_random_uuid(),
  kind          text not null,
  league_slug   text,
  season        text,
  issue_key     text,
  state         text not null default 'running',
  stages        jsonb not null default '[]'::jsonb,
  result        jsonb,
  error         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  ended_at      timestamptz
);

create index if not exists jobs_recent_idx on jobs (kind, created_at desc);

comment on column jobs.state is 'running | succeeded | failed';

-- ------------------------------------------------- lock everything down

do $$
declare t text;
begin
  foreach t in array array[
    'app_commissioners','issues','issue_modules','sections','prose_revisions',
    'issue_revision_requests','team_names','story_decisions','award_decisions',
    'matchup_state','power_rankings','takes','editorial_usage','bit_usage',
    'editorial_meta','jobs'
  ] loop
    execute format('alter table %I enable row level security', t);
    -- FORCE applies RLS even to the table owner, so a privileged connection
    -- cannot quietly read around the policy.
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists commissioner_all on %I', t);
    execute format(
      'create policy commissioner_all on %I for all to authenticated '
      'using (app_is_commissioner()) with check (app_is_commissioner())', t);
    -- the publishable/anon role gets nothing, ever
    execute format('revoke all on %I from anon', t);
    execute format('grant select, insert, update, delete on %I to authenticated', t);
  end loop;
end $$;

revoke all on function app_is_commissioner() from anon;

-- Sequences used by bigserial columns must not be reachable by anon either.
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
