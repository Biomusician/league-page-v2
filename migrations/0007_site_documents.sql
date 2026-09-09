-- League-Page — site-wide authored documents (Supabase / Postgres)
-- Migration 0007. Additive and idempotent: safe to run more than once.
--
-- HOW TO APPLY (no credential leaves your machine):
--   Supabase dashboard -> SQL Editor -> New query -> paste this file -> Run.
--   Expect "Success. No rows returned".
--
-- WHY THIS EXISTS
--   The Desk's Site -> About editor is the last authoring surface that
--   writes authoritative state to this machine's filesystem. It persists to
--   `editorial/site/about.md`, and `leaguepage/site_build.py` reads the same
--   file when it builds `about/index.html`. On a hosted Desk that file does
--   not survive a restart, so the Commissioner's own words about how the
--   paper is made would be the one thing Hosted Beta silently discarded.
--
--   Every other authoring route already lands in a table. This closes the
--   last one, and nothing else.
--
-- WHY A TABLE OF ITS OWN, AND NOT ONE OF THE ONES ALREADY HERE
--   `sections` / `prose_revisions` are addressed by a ProseKey, which is
--   (league, season, issue, kind, name). About has no league, no season and
--   no issue. Giving it invented values would put a lie in the primary key
--   of the table the whole editorial model is addressed by, and every
--   readiness count, diff and parity check reads that table.
--
--   `research_artifacts` is issue-scoped research that arrives from outside
--   the Desk. About is neither issue-scoped nor research.
--
--   `editorial_meta` is recomputable settings. The import deliberately does
--   not compare it, for that reason. Authored prose kept in a table the
--   parity checks skip is authored prose nothing is guarding.
--
-- WHAT THIS IS DELIBERATELY NOT
--   Not a CMS. No revisions, no drafts, no publish workflow, no ordering,
--   no per-league scoping, no titles, no routing. One row per site-wide
--   authored document, and today there is exactly one: 'about'. A second
--   document needs a row, not a schema change; a second FEATURE (history,
--   scheduling, per-league copy) needs a decision, and should not be
--   reachable by accident because the table was built wide enough for it.
--
--   The one thing consciously given up: on the filesystem, About has git
--   history. Here it has `updated_at` and `updated_by` and no revision log.
--   That is a real reduction and it is deliberate -- the Desk has never had
--   an About history UI, and inventing a revision table to preserve a
--   history nothing reads would be the speculative half of this migration.
--   `docs/CUTOVER.md` records it as a named loss rather than an oversight.

create table if not exists site_documents (
  slug       text primary key,
  body       text not null default '',
  updated_at timestamptz not null default now(),
  updated_by text
);

comment on table site_documents is
  'Site-wide authored copy that belongs to no league, season or issue.
   Exactly one row today: slug = ''about'', the public methodology and AI
   disclosure page. Authoritative in Postgres mode; the filesystem backend
   keeps the same text at editorial/site/about.md. Not a CMS: no revisions,
   no drafts, no workflow.';

comment on column site_documents.body is
  'Markdown, exactly as the Commissioner typed it. Rendered by prose.render
   for both the Desk preview and the public about/index.html.';

comment on column site_documents.updated_by is
  'The acting Commissioner''s email, as every other editorial write records
   it. Not a revision log -- it says who wrote what is there now.';

-- ------------------------------------------------------------- lock it down

-- The same `do $$` block 0001 and 0006 use, deliberately: one shape, so no
-- table can be added with a policy that quietly differs from the others.
do $$
declare t text;
begin
  foreach t in array array['site_documents'] loop
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

-- The smaller credential 0006 created, if this project's SQL Editor role
-- was allowed to create it. Guarded the same way and for the same reason:
-- a missing optional role must not block the schema work above.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'leaguepage_app') then
    grant select, insert, update, delete on site_documents to leaguepage_app;
  end if;
exception when insufficient_privilege then
  raise notice 'site_documents not granted to leaguepage_app: this role '
               'lacks the privilege. The table and its policy are applied.';
end $$;

-- --------------------------------------------------------------- assert it

-- Fail loudly rather than leaving a table readable.
do $$
declare ok boolean;
begin
  select c.relrowsecurity and c.relforcerowsecurity into ok
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relname = 'site_documents';
  if not ok then
    raise exception 'RLS is not enabled AND forced on site_documents';
  end if;

  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and tablename = 'site_documents'
                    and policyname = 'commissioner_all') then
    raise exception 'site_documents has no commissioner_all policy';
  end if;

  -- anon must hold no privilege on the table at all, not merely be blocked
  -- by a policy it could be granted around later.
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'site_documents'
                and grantee = 'anon') then
    raise exception 'anon still holds a grant on site_documents';
  end if;
end $$;
