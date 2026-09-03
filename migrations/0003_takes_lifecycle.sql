-- 0003: takes gain a lifecycle, a subject, a horizon and a public flag.
--
-- The takes table already existed (0001) as quote + subject + status. This
-- turns it into the receipts engine: what the claim is about, when it is
-- worth re-checking, what the engine computed last time, and whether the
-- Commissioner has cleared it for public surfaces.
--
-- Two boundaries are enforced by the shape of this table rather than by
-- convention:
--   * `status` is the Commissioner's verdict and `recommended_status` is the
--     engine's proposal. They are separate columns so a disagreement is
--     visible instead of silently overwritten.
--   * `public` defaults to 0. Nothing reaches a reader without a deliberate
--     act, so an unreviewed take cannot leak onto the site.
--
-- Idempotent: safe to run against a database that already has 0001 applied.

alter table takes add column if not exists context            text;
alter table takes add column if not exists author             text;
alter table takes add column if not exists players            text;
alter table takes add column if not exists topic              text;
alter table takes add column if not exists issue_key          text;
alter table takes add column if not exists section            text;
alter table takes add column if not exists subject_type       text;
alter table takes add column if not exists subject_name       text;
alter table takes add column if not exists subject_roster_id  integer;
alter table takes add column if not exists review_after       text;
alter table takes add column if not exists review_week        integer;
-- 1 = the quote is the published text unchanged. 0 = the Commissioner
-- paraphrased it, and public surfaces must not present it as a quotation.
alter table takes add column if not exists verbatim           integer not null default 1;
alter table takes add column if not exists evidence           text;
alter table takes add column if not exists last_evaluated_at  timestamptz;
alter table takes add column if not exists recommended_status text;
alter table takes add column if not exists public             integer not null default 0;
alter table takes add column if not exists href               text;
alter table takes add column if not exists note               text;
alter table takes add column if not exists resolution         text;
alter table takes add column if not exists resolved_at        timestamptz;

-- Pre-lifecycle vocabulary -> the seven canonical statuses.
update takes set status = 'resolved_right'  where status = 'validated';
update takes set status = 'resolved_wrong'  where status = 'contradicted';
update takes set status = 'void'            where status = 'retired';

create index if not exists takes_league_season_idx on takes (league_slug, season);
create index if not exists takes_open_idx
  on takes (league_slug, status)
  where status in ('open', 'too_early', 'leaning_right', 'leaning_wrong');
