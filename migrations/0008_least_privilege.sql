-- League-Page — grant `authenticated` exactly what the app needs
-- Migration 0008. Idempotent: safe to run more than once.
--
-- HOW TO APPLY (no credential leaves your machine):
--   Supabase dashboard -> SQL Editor -> New query -> paste this file -> Run.
--   Expect "Success. No rows returned".
--
-- WHY THIS EXISTS
--   Every migration since 0001 has said the same thing:
--
--       grant select, insert, update, delete on <table> to authenticated;
--
--   None of them said what NOT to grant, and a new Supabase project ships
--   with default privileges that hand `anon`, `authenticated` and
--   `service_role` ALL privileges on every table created in `public`. The
--   migrations revoked anon. Nothing revoked the surplus from
--   `authenticated`, so all 22 tables carry TRUNCATE, REFERENCES and
--   TRIGGER for that role on top of the four the application uses.
--
--   Measured on 2026-09-09, all 22 tables, before this migration:
--       authenticated: DELETE, INSERT, REFERENCES, SELECT, TRIGGER,
--                      TRUNCATE, UPDATE
--       anon:          (none)
--       leaguepage_app: DELETE, INSERT, SELECT, UPDATE   <- what was asked for
--
--   TRUNCATE is the one that matters, because **row level security does
--   not apply to TRUNCATE**. The `commissioner_all` policy stops a
--   non-Commissioner deleting a single row and would not stop the same
--   role emptying the table. TRIGGER is the quieter one: a trigger runs
--   with the privileges of whoever defined it.
--
-- HOW BAD IS IT TODAY
--   Not open. Acting as `authenticated` needs either a Postgres connection
--   -- which needs the DSN -- or PostgREST with a user JWT, and PostgREST
--   only ever issues SELECT/INSERT/UPDATE/DELETE. It cannot TRUNCATE and
--   cannot create a trigger. So this is the difference between what the
--   migrations claim to grant and what the database actually grants, which
--   is worth closing before a hosted Desk exists rather than after.
--
-- WHY `revoke all` AND RE-GRANT, RATHER THAN NAMING THE SURPLUS
--   Naming privileges to remove means keeping that list in step with the
--   server version -- MAINTAIN arrived in PostgreSQL 17 and would have to
--   be added, guarded, and remembered. Stating the whole intended set
--   instead is idempotent, version-independent, and says what it means:
--   these four, and nothing else.

do $$
declare t text;
begin
  for t in
    select c.relname from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r'
     order by c.relname
  loop
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke all on public.%I from authenticated', t);
    execute format(
      'grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;

-- ------------------------------------------- and for the tables not yet made

-- Without this the next `create table` re-acquires the surplus and 0009
-- inherits the same defect. This changes the defaults for objects created
-- by THIS role in THIS schema, which is exactly the set the migrations
-- create; nothing else in the project is touched.
alter default privileges in schema public
  revoke all on tables from anon;
alter default privileges in schema public
  revoke all on tables from authenticated;
alter default privileges in schema public
  grant select, insert, update, delete on tables to authenticated;

-- --------------------------------------------------------------- assert it

-- The check 0007 should have made and did not: it asserted anon held
-- nothing and said nothing about the role that actually writes.
do $$
declare t text; extra text[]; bad text[] := '{}';
begin
  for t in
    select c.relname from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r'
  loop
    select array_agg(privilege_type order by privilege_type) into extra
      from information_schema.role_table_grants
     where table_schema = 'public' and table_name = t
       and grantee = 'authenticated'
       and privilege_type not in ('SELECT','INSERT','UPDATE','DELETE');
    if extra is not null then
      bad := bad || (t || ':' || array_to_string(extra, ','));
    end if;

    if exists (select 1 from information_schema.role_table_grants
                where table_schema = 'public' and table_name = t
                  and grantee = 'anon') then
      bad := bad || (t || ':anon-still-granted');
    end if;
  end loop;

  if array_length(bad, 1) is not null then
    raise exception 'least privilege not achieved: %', array_to_string(bad, ' ');
  end if;
end $$;
