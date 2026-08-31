# Schema migrations

Versioned SQL for the Supabase/Postgres store that backs remote Commissioner
authoring. **These files are safe source code and are committed. Credentials
never are.**

Apply them in numeric order.

## How to apply (no credential leaves your machine)

Supabase dashboard → **SQL Editor** → New query → paste the file → **Run**.

This is the recommended path for `0001`: it needs no `DATABASE_URL`, no
service-role key, and nothing sensitive typed into a terminal or a chat.
Every statement is idempotent, so re-running is safe.

After `0001`, add yourself to the allowlist in the same SQL editor — this
is the row that actually authorizes access, and it must match
`LEAGUEPAGE_COMMISSIONER_EMAILS`:

```sql
insert into app_commissioners (email, note)
values ('you@example.com', 'commissioner')
on conflict (email) do nothing;
```

## Files

| File | What it does |
|---|---|
| `0001_commissioner_state.sql` | Authoritative editorial tables (issues, modules, **sections** — where prose moves off the filesystem — revisions, decisions, overrides, rankings, editorial meta) plus the durable `jobs` table. Enables **and forces** RLS on every table with a single policy that requires an authenticated user on the `app_commissioners` allowlist. Grants `anon` nothing. |

## Security notes worth keeping in mind

- RLS is **forced**, not merely enabled, so even an owner-level connection
  cannot read around the policy.
- The policy is not "any authenticated user" — it checks the JWT email
  against `app_commissioners`. A valid Supabase account that is not listed
  reads nothing.
- The publishable/anon key is granted nothing on any table or sequence, so
  even if it were exposed it cannot reach editorial state.
- `sections.version` exists for optimistic concurrency: a save carrying a
  stale version must be refused rather than overwrite a newer edit from
  another tab or an email proposal.
