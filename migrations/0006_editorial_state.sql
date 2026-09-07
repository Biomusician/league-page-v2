-- League-Page — unified cloud editorial state (Supabase / Postgres)
-- Migration 0006. Additive and idempotent: safe to run more than once.
--
-- HOW TO APPLY (no credential leaves your machine):
--   Supabase dashboard -> SQL Editor -> New query -> paste this file -> Run.
--   Expect "Success. No rows returned".
--
-- WHY THIS EXISTS
--   Tranche 5A proved a semantic contract locally: one Commissioner action
--   commits or does not commit, and every claim about prose carries the
--   identity of the prose it describes. Postgres cannot implement that
--   contract while three of the tables the contract is about do not exist
--   here and four of the columns are missing. This closes exactly those
--   measured gaps and nothing else.
--
--   `tests/test_schema_parity.py` declares every difference between the
--   SQLite schema and this one, with its reason. After this migration the
--   only declared differences left are deliberate.
--
-- WHAT IS DELIBERATELY NOT HERE
--   section_prose_state. Postgres already carries that meaning on
--   `sections.state`; the gap was never a missing table, it was a missing
--   caller -- the Desk wrote set_prose_state() to SQLite while the
--   repository wrote only content and version. That is code, not schema.

-- ----------------------------------------------------- measured gaps: columns

-- An approval is a statement about a particular text, so it records which
-- text. Added to SQLite on 2026-09-05 for Common Tactical Picture and
-- generalised to every approvable module in Tranche 5A; without it here,
-- the one mechanism that retires itself correctly could not migrate.
alter table issue_modules add column if not exists approved_sha text;

-- The optional issue-wide gimmick. Editorial intent, not a derivation.
alter table issues add column if not exists theme text;

-- Structured requests carried into the next drafting pass. TEXT rather than
-- JSONB on purpose: SQLite stores the JSON as a string and the import has to
-- round-trip byte for byte, so a type conversion in the middle is a bug
-- waiting to be found by a parity check.
alter table matchup_state add column if not exists revision_requests text;

-- What each preview said when Common Tactical Picture was approved over all
-- of them. NOT a per-preview approval -- there is exactly one approval and
-- it is CTP's -- this is a record of what that approval covered, so a card
-- can name the preview that moved instead of flagging every one of them.
alter table matchup_state add column if not exists covered_sha text;

comment on column issue_modules.approved_sha is
  'Signature of exactly what was approved. Approval means approved = true
   AND approved_sha = signature(current publishable content). A row with
   approved = true and a NULL signature predates signatures: historical
   fact, not evidence about the current text.';
comment on column matchup_state.covered_sha is
  'What this preview said when CTP was approved over all of them. Coverage
   evidence, not an independent approval.';

-- ------------------------------------------------------ measured gaps: tables

-- The authorship claim. Content-bound by design: `generated_sha` is a hash
-- of the text the claim is about, so a claim whose subject moved simply
-- stops applying and nothing has to notice. This is the pattern Tranche 5A
-- generalised to approval, and it is why a half-completed Commissioner
-- click is silent rather than wrong.
create table if not exists prose_provenance (
  league_slug   text not null,
  season        text not null,
  issue_key     text not null,
  section       text not null,
  generator     text,
  method        text,
  generated_sha text not null default '',
  recorded_at   timestamptz not null default now(),
  origin        text,
  assistance    text,
  baseline_text text,
  event         text,
  primary key (league_slug, season, issue_key, section)
);

comment on column prose_provenance.baseline_text is
  'PRIVATE. The generated text the Desk measures his edits against. It must
   never reach a published snapshot, a reader page or any unauthenticated
   response.';

-- The Commissioner's optional blurb on one Sleeper transaction.
create table if not exists force_flow_notes (
  league_slug text not null,
  season      text not null,
  txn_id      text not null,
  note        text not null,
  updated_at  timestamptz not null default now(),
  primary key (league_slug, season, txn_id)
);

-- Research that arrives from OUTSIDE the Desk and cannot be recomputed.
-- Deliberately not a document store: five named artifacts per issue with
-- one writer and one reader each, no versioning, no search, no undo. The
-- recomputable briefs and packets stay out -- a worker regenerates those.
--
-- `scope` is 'lowdown' or 'matchup:<slug>'; `name` is the filename.
create table if not exists research_artifacts (
  league_slug text not null,
  season      text not null,
  issue_key   text not null,
  scope       text not null,
  name        text not null,
  body        text not null default '',
  updated_at  timestamptz not null default now(),
  primary key (league_slug, season, issue_key, scope, name)
);

comment on table research_artifacts is
  'Unrecomputable research inputs. rough-lowdown.md is the one that matters:
   its EXISTENCE decides the Lowdown workflow status and whether provenance
   records that AI assistance reached the section, so a hosted Desk that
   cannot see it gets both of those wrong.';

create index if not exists prose_provenance_issue_idx
  on prose_provenance (league_slug, season, issue_key);
create index if not exists research_artifacts_issue_idx
  on research_artifacts (league_slug, season, issue_key);

-- ------------------------------------------------- the guard function's grants

-- `revoke all ... from anon` in 0001 did not do what it says: PostgreSQL
-- grants EXECUTE on functions to PUBLIC by default, and revoking from one
-- role does not remove the PUBLIC grant. Nothing leaked -- the function
-- returns a boolean about the caller's own JWT and anon has no JWT -- but
-- the migration should do what it claims.
revoke all on function app_is_commissioner() from public;
revoke all on function app_is_commissioner() from anon;
grant execute on function app_is_commissioner() to authenticated;

-- ------------------------------------------------------------- lock them down

-- Same shape as 0001, deliberately: one block, so nothing is locked down by
-- hand and no new table can be added with a policy that quietly differs.
do $$
declare t text;
begin
  foreach t in array array[
    'prose_provenance','force_flow_notes','research_artifacts'
  ] loop
    execute format('alter table %I enable row level security', t);
    -- FORCE applies RLS even to the table owner, so a privileged connection
    -- cannot quietly read around the policy.
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists commissioner_all on %I', t);
    execute format(
      'create policy commissioner_all on %I for all to authenticated '
      'using (app_is_commissioner()) with check (app_is_commissioner())', t);
    execute format('revoke all on %I from anon', t);
    execute format('grant select, insert, update, delete on %I to authenticated', t);
  end loop;
end $$;

-- ------------------------------------------- a smaller credential, for later

-- THE RUNTIME AUTHORIZATION BOUNDARY, stated so it is chosen rather than
-- inherited: the application asserts the signed-in Commissioner's identity
-- into every transaction --
--
--     set local role authenticated;
--     set local request.jwt.claims = '{"email": "..."}';
--
-- -- so the policy above evaluates exactly as it would for a browser. RLS
-- is bypassed on the basis of the CURRENT role, not the login role, so this
-- binds even on the owner connection. Verified 2026-09-08: an owner
-- connection that assumes `authenticated` with a non-allowlisted email
-- reads zero rows.
--
-- A real transaction is still required, because one Commissioner action
-- spans six tables and PostgREST gives each request its own transaction.
-- That is why the application holds a login rather than a JWT.
--
-- This role exists so the credential blast radius can be reduced later
-- WITHOUT a code change: give it a password out of band, point
-- DATABASE_URL at it, and a leaked connection string stops being able to
-- read auth.users or drop a table. It is created NOLOGIN precisely so that
-- committing this file grants nobody anything.
-- Guarded as a whole: creating a role needs CREATEROLE, and if this
-- project's SQL Editor role does not have it, that must not block the
-- schema work above, which is the actual point of the migration. A notice
-- is enough -- nothing today depends on this role existing.
do $$
declare t text;
begin
  if not exists (select 1 from pg_roles where rolname = 'leaguepage_app') then
    create role leaguepage_app nologin;
  end if;
  grant usage on schema public to leaguepage_app;
  grant authenticated to leaguepage_app;   -- so it may SET ROLE authenticated

  foreach t in array array[
    'app_commissioners','issues','issue_modules','sections','prose_revisions',
    'issue_revision_requests','team_names','story_decisions','award_decisions',
    'matchup_state','power_rankings','takes','editorial_usage','bit_usage',
    'editorial_meta','jobs','job_events','sync_snapshots',
    'prose_provenance','force_flow_notes','research_artifacts'
  ] loop
    if to_regclass('public.' || t) is not null then
      execute format('grant select, insert, update, delete on %I to leaguepage_app', t);
    end if;
  end loop;
exception when insufficient_privilege then
  raise notice 'leaguepage_app not created: this role lacks CREATEROLE. The '
               'schema above is applied; the smaller credential can wait.';
end $$;

do $$
declare s text;
begin
  for s in select sequence_name from information_schema.sequences
           where sequence_schema = 'public'
  loop
    execute format('revoke all on sequence %I from anon', s);
    execute format('grant usage, select on sequence %I to authenticated', s);
    execute format('grant usage, select on sequence %I to leaguepage_app', s);
  end loop;
end $$;

-- --------------------------------------------------------------- assert it

-- Fail loudly rather than leaving a table readable. RLS must be enabled AND
-- forced on everything this migration created.
do $$
declare t text; ok boolean;
begin
  foreach t in array array[
    'prose_provenance','force_flow_notes','research_artifacts'
  ] loop
    select c.relrowsecurity and c.relforcerowsecurity into ok
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relname = t;
    if not ok then
      raise exception 'RLS is not enabled AND forced on %', t;
    end if;
  end loop;
  if has_function_privilege('anon', 'public.app_is_commissioner()', 'execute') then
    raise exception 'anon can still execute app_is_commissioner()';
  end if;
end $$;
