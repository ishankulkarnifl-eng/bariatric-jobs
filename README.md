# Bariatric Jobs Dashboard

A zero-infrastructure pipeline that aggregates full-time bariatric / MIS / foregut
surgeon positions nationally (all practice models except locums), normalizes them
with Claude, and publishes a private dashboard plus a morning email digest of new
listings.

## Architecture

```
GitHub Actions (nightly cron)
  └─ pipeline/run.py
       ├─ sources.py    SerpAPI Google Jobs (primary) + USAJobs + ASMBS Career Center
       ├─ extract.py    Claude Haiku → strict schema (comp, model, call, MBSAQIP,
       │                robotics, fellowship, visa) + relevance/FT/locums filtering
       ├─ store.py      dedupe on employer+state+title, first_seen/last_seen,
       │                repost detection, staleness archiving  →  data/listings.json
       ├─ cluster.py    one job, one card: groups the postings that describe the
       │                same opening (presentation only, never mutates the store)
       ├─ digest.py     Resend email of NEW listings only (silent when nothing new)
       └─ site_builder/ regenerates docs/index.html (served by GitHub Pages)
```

No servers, no database. State lives in `data/listings.json`, committed each night.
`first_seen`/`last_seen` timestamps power the analytics no job board provides:
days-on-market, NEW badges, and repost counts.

## Setup (~20 minutes)

1. **Create a private GitHub repo** and push this directory.
2. **Enable GitHub Pages**: Settings → Pages → Deploy from branch → `main` /`/docs`.
   (Note: Pages on a private repo requires GitHub Pro/Team; on a free account,
   deploy `docs/` to Cloudflare Pages instead, or make the repo public — the
   dashboard contains only public job postings and is `noindex`.)
3. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `ANTHROPIC_API_KEY` — required (extraction)
   - `SERPAPI_KEY` — required in practice; this is the primary source (free tier is
     250 searches/month and sufficient at current settings — 4 queries × 1 page
     nightly ≈ 124/month; paid tier is $25/mo for 1,000 searches if you expand)
   - `RESEND_KEY` — for the email digest (free tier is ample); verify a sending
     domain in Resend and set `digest.from` in `config.yaml`
   - `USAJOBS_KEY` + `USAJOBS_EMAIL` — optional, free at developer.usajobs.gov
   - Repo **variable** `DASHBOARD_URL` — your Pages URL, linked in the digest
4. **Edit `config.yaml`**: digest recipient, sending address, queries if desired.
5. **Run it once manually**: Actions → "Nightly job scan" → Run workflow.
   The sample data in `data/listings.json` is replaced on the first successful run.

## Running locally

```bash
pip install -r requirements.txt
python -m unittest discover -s tests   # verify
ANTHROPIC_API_KEY=... SERPAPI_KEY=... python -m pipeline.run
python -m site_builder.build_site   # rebuild dashboard only
open docs/index.html
```

## Costs

SerpAPI is the only meaningful cost: the free tier (250 searches/month) is
sufficient at current settings; the paid tier is $25/mo for 1,000 searches.
Claude Haiku extraction is well under $1/mo at this volume. Resend, USAJobs,
GitHub Actions: free.

## Maintenance expectations

- `sources.fetch_asmbs` is a best-effort HTML parse of a career-site platform;
  if its count sits at 0 for a couple of weeks, the selectors need a refresh.
- **Apply links.** Google hands Bright Data its own *relative* redirect
  (`/goto?url=<token>`), which is a dead link anywhere off a Google SERP, and it
  mints a fresh token per fetch, so an unresolved token also reads as a nightly
  repost. `sources.resolve_job_url` follows it to the employer/board URL and
  returns `""` for anything it cannot vouch for, including a consent wall or a
  `google.com/search` link. `store.usable_url` keeps those out of the store, so
  a listing with no resolvable link renders "No link captured" rather than a
  button that goes nowhere, and retries the next night with a fresh token.
  `pipeline.run` resolves only listings it has no link for (~15 a night, not one
  per raw hit) and logs a warning if most resolutions fail, which is what
  Google rate-limiting the CI runner would look like. **If you see a run of
  "No link captured" on the dashboard, that is the signal.**
  Covered by `tests/test_apply_links.py`.
- **One job, one card.** `store.listing_key` is an exact `employer::state::title`
  match, and every aggregator spells the employer differently, so one Lenexa
  opening sat in the store as seven rows ("AdventHealth", "AdventHealth Kansas",
  "AdventHealth Medical Group - Kansas", …). `pipeline/cluster.py` groups the
  postings that describe the same job: same state and city, overlapping employer
  tokens once noise words are stripped, and compatible role signatures. On
  2026-09-20 that was 146 active postings for 115 real jobs.
  Grouping is complete-linkage: a posting joins a card only if it matches every
  posting already on it. Chaining A to C through B put a bariatric role and a
  plain general-surgery role on one card; the stricter rule cost two cards out
  of 115 and removed that entirely.
  This happens at **presentation time only** and never touches the store, so a
  bad grouping is a code fix rather than a data recovery, and every posting stays
  visible behind "Listed N times", and the collapsed card carries an "also listed
  as" line naming what it folded in, so a wrong grouping is catchable while
  scanning rather than only on inspection. A card's identity is its earliest posting, not
  its representative, so a saved status survives a better posting arriving later.
  The digest uses the same rule. Once a digest is actually delivered, every
  posting of each announced job is flagged `announced` in the store, so no later
  wording of it is emailed again - `first_seen` cannot be trusted for this,
  because `store.merge` backdates it to the posting date and a wording arriving
  tonight can look older than the twin sent last week. If real openings ever start disappearing, suspect
  `cluster.same_job` and widen it; `tests/test_cluster.py` pins the cases that
  must NOT merge.
- `repost_count` is still stored but no longer shown. It counts distinct URLs,
  which after link resolution is mostly syndication breadth (several aggregators
  carrying one posting), not recruiter churn. Badge dropped 2026-09-20; bring it
  back when there is a signal that means what the name says.
- Update `benchmarks.comp_median_usd` in `config.yaml` annually.
- Saved/applied statuses are stored in the browser (localStorage) — single-device
  by design for v1. If she wants cross-device status or you want richer analytics,
  that's the trigger to promote this into the Django/Celery stack.

## Sample data

`data/listings.json` ships with **fictional** listings (flagged `meta.sample: true`,
with a banner on the dashboard) so the UI renders before any keys are configured.
