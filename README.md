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
- Update `benchmarks.comp_median_usd` in `config.yaml` annually.
- Saved/applied statuses are stored in the browser (localStorage) — single-device
  by design for v1. If she wants cross-device status or you want richer analytics,
  that's the trigger to promote this into the Django/Celery stack.

## Sample data

`data/listings.json` ships with **fictional** listings (flagged `meta.sample: true`,
with a banner on the dashboard) so the UI renders before any keys are configured.
