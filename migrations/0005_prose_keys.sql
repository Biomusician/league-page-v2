-- 0005: prose gains a kind, so one table can hold everything he writes.
--
-- 0001 created `sections` as league/season/issue/section -> content, which
-- assumed prose was only ever a module's copy. It is not. The Commissioner
-- also writes a preview for every matchup, and Claude Code and ChatGPT hand
-- back proposals that wait for his verdict. All three are prose objects with
-- the same lifecycle: read, edit against a version, keep a revision, publish
-- or discard. The filesystem told them apart by directory
-- (`sections/`, `matchups/<slug>/`, `proposals/`); a table needs a column.
--
-- `kind` is that column, and it joins the primary key. Without it,
-- `sections/fades` and `proposals/fades` collide: accepting a proposal would
-- overwrite the very text it was proposed against.
--
-- The section identifier itself is unchanged and deliberately so. A matchup
-- is `matchup:<slug>` here exactly as it has always been in
-- `prose_revisions`, `section_prose_state`, `prose_provenance` and
-- `issue_modules`. Those four tables key editorial metadata by that string,
-- and this migration is not a metadata migration: the words move, their
-- history and provenance keep their existing addresses.
--
-- `version` was already here and is what optimistic concurrency counts. A
-- write is `... where version = expected` and a rowcount of zero is the
-- conflict; nothing reads a version and updates in a later statement.
--
-- Idempotent, and safe against a database that already has 0001 applied.

alter table sections add column if not exists kind text not null default 'section';

comment on column sections.kind is
  'section | matchup | proposal. Part of the identity: a proposal for a '
  'section is a different object from the section, and overwriting one with '
  'the other is exactly the bug the column prevents.';

-- Existing rows predate proposals and matchups, so the default is already
-- right for them; this only makes the intent explicit for a reader.
update sections set kind = 'section' where kind is null;

-- Re-key on the fuller identity. Postgres names a primary key
-- <table>_pkey unless told otherwise, and 0001 did not tell it otherwise.
do $$
begin
  if exists (
    select 1 from pg_constraint
     where conrelid = 'sections'::regclass and contype = 'p'
       and array_length(conkey, 1) = 4
  ) then
    alter table sections drop constraint sections_pkey;
    alter table sections add primary key (league_slug, season, issue_key, kind, section);
  end if;
end $$;

-- What `list_issue` asks for: everything one issue holds, in one pass. The
-- primary key already leads with these three columns, so this is only worth
-- adding if the key is ever re-ordered; kept explicit so the access pattern
-- is written down rather than inferred.
create index if not exists sections_issue_idx
  on sections (league_slug, season, issue_key, kind);

-- Revisions are addressed by the same section string the filesystem backend
-- uses, so no change is needed there. The index from 0001 already covers the
-- lookup; this records why it is the right shape.
comment on table prose_revisions is
  'Undo history for prose, keyed by the same section identifier the '
  'repository exposes (matchups as matchup:<slug>). Written in the same '
  'transaction as the prose it replaces, so an edit that promises history '
  'cannot half-happen.';

-- sections and prose_revisions are already in 0001's forced-RLS list, and
-- this migration adds no table, so the policy surface is unchanged. Assert
-- it rather than assume it: a prose table readable by anon would publish
-- unreviewed drafts.
do $$
declare t text;
begin
  foreach t in array array['sections', 'prose_revisions'] loop
    if not exists (select 1 from pg_class where relname = t and relrowsecurity) then
      raise exception 'RLS is not enabled on %', t;
    end if;
    if not exists (select 1 from pg_class where relname = t and relforcerowsecurity) then
      raise exception 'RLS is not FORCED on %', t;
    end if;
    execute format('revoke all on %I from anon', t);
  end loop;
end $$;
