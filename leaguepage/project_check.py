"""Do Auth and the database belong to the same Supabase project?

WHY THIS EXISTS

On 2026-09-09 the answer here was no, and nothing said so. `DATABASE_URL`
pointed at the project holding every migration and all 842 imported
editorial rows; `SUPABASE_URL` pointed at a different project, which
carried only migration 0001's tables and was where sign-in happened.

Be precise about what that does and does not break, because the obvious
reading is wrong. `app_is_commissioner()` reads
`auth.jwt() ->> 'email'`, and the value there is the one the APPLICATION
asserted with `set_config('request.jwt.claims', ...)` from its own
session. The database never verifies a Supabase-issued token, so a
mismatch does not let a stranger past RLS.

What it does mean is that authentication and authorization live in
different projects with no link between them. The allowlist that decides
access sits in project A; the account that vouches for the person sits in
project B; and nothing anywhere states that they are supposed to be the
same. Every operational question then has two answers -- which dashboard
shows the sign-ins, which project's rate limits apply, which one to
disable a compromised account in -- and the split survived weeks precisely
because nothing broke loudly.

It also made a diagnostic lie for weeks: `verify_supabase_schema.py` asks
one project over PostgREST and the other over SQL, and reported the six
tables the auth project has never had as "PostgREST's schema cache".

WHAT IS COMPARED, AND WHAT IS NEVER PRINTED

A Supabase project ref is public -- it is the subdomain of the project's
own URL -- but a DSN is SECRET-class, so the ref is derived and compared
without the DSN ever being formatted into a message. `describe()` returns
a verdict and, at most, the two refs; `problem()` returns one sentence
that names neither a host nor a credential. Callers that render to a
reader (the health endpoint) take the verdict only.

WHERE THE REF LIVES

  a pooled connection   the USERNAME, as `postgres.<ref>`
  a direct connection   the HOSTNAME, as `db.<ref>.supabase.co`
  the API URL           the SUBDOMAIN, as `<ref>.supabase.co`

Stated rather than illustrated, because a worked example here would be a
connection string in a tracked file and `scripts/audit_repo_privacy.py` is
right to refuse those.

The pooled case is the one that matters: it is what this project uses, and
a hostname check alone would call every pooled project "unknown" and pass
a mismatch through. A custom or self-hosted connection has no ref at all;
that is `unknown`, not `mismatch`, because refusing to start over a string
this module cannot parse would be this module inventing a policy.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from leaguepage import settings

MATCH, MISMATCH, UNKNOWN, ABSENT = "match", "mismatch", "unknown", "absent"

# "the caller did not say" has to be distinguishable from "the caller
# said there is none". Reading settings when a caller passed an explicit
# None makes the answer depend on ambient configuration, which is how a
# test of this module started passing and failing by which other tests
# ran first.
_UNSET = object()

_DB_HOST = re.compile(r"^db\.([a-z0-9]{16,})\.supabase\.(co|com|net)$", re.I)
_API_HOST = re.compile(r"^([a-z0-9]{16,})\.supabase\.(co|com|net)$", re.I)


def ref_of_dsn(dsn: str | None) -> str | None:
    """The project ref a Postgres DSN belongs to, or None."""
    if not dsn:
        return None
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return None
    user = parsed.username or ""
    if user.startswith("postgres.") and len(user) > len("postgres."):
        return user.split(".", 1)[1].lower()
    m = _DB_HOST.match(parsed.hostname or "")
    return m.group(1).lower() if m else None


def ref_of_url(url: str | None) -> str | None:
    """The project ref a Supabase API URL belongs to, or None."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return None
    m = _API_HOST.match(host)
    return m.group(1).lower() if m else None


def describe(dsn=_UNSET, url=_UNSET) -> dict:
    """{verdict, database_ref, auth_ref}. Refs are public; a DSN is not,
    and no part of one appears here.

    Called with no arguments it reads the configuration. Called with an
    explicit None it means that value is genuinely absent.
    """
    if dsn is _UNSET:
        dsn = settings.get(settings.DATABASE_URL)
    if url is _UNSET:
        url = settings.get(settings.SUPABASE_URL)
    if not dsn or not url:
        return {"verdict": ABSENT, "database_ref": None, "auth_ref": None}
    a, b = ref_of_dsn(dsn), ref_of_url(url)
    if a is None or b is None:
        return {"verdict": UNKNOWN, "database_ref": a, "auth_ref": b}
    return {"verdict": MATCH if a == b else MISMATCH,
            "database_ref": a, "auth_ref": b}


def problem(dsn=_UNSET, url=_UNSET) -> str:
    """One sentence when the two disagree, empty otherwise.

    Names the two refs, because a ref is public and an operator who cannot
    see WHICH projects disagree cannot fix it. Never the DSN, the host, the
    user or the password.
    """
    d = describe(dsn, url)
    if d["verdict"] != MISMATCH:
        return ""
    return (f"Supabase project mismatch: the database is project "
            f"{d['database_ref']} and Auth is project {d['auth_ref']}. "
            f"The allowlist and every RLS policy live in the database "
            f"project; the accounts that sign people in live in the other "
            f"one, with nothing linking them. Point SUPABASE_URL and "
            f"SUPABASE_PUBLISHABLE_KEY at {d['database_ref']}.")


def enforce(*, backend: str, auth_required: bool,
            dsn=_UNSET, url=_UNSET) -> str:
    """Refuse the configuration that a mismatch actually breaks.

    Raising on every mismatch would stop the Desk this machine runs today,
    which signs nobody in and reads its own filesystem -- neither half is
    in use there, and a config error the operator cannot act on without
    stopping work is a config error that gets worked around.

    So it refuses exactly the combination where both halves are live: a
    Postgres backend, or sign-in switched on. Everything else gets the
    sentence back to surface, and it is up to the caller whether to warn.
    """
    message = problem(dsn, url)
    if not message:
        return ""
    if backend == "postgres" or auth_required:
        raise RuntimeError(message)
    return message
