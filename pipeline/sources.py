"""Source adapters. Each returns a list of RawListing dicts:
{source, source_id, title, employer, location, description, url, posted_hint}

Design note: we deliberately use ONE paid aggregator (Google Jobs via SerpAPI)
instead of N bespoke scrapers. Google Jobs already crawls hospital ATS systems
(Workday, iCIMS, Greenhouse), Indeed, LinkedIn, and most physician boards.
ASMBS and USAJobs are added for niche/federal coverage the aggregator misses.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

import requests

log = logging.getLogger("sources")
UA = "bariatric-jobs-pipeline/1.0 (personal job-search tool)"


def _rid(*parts: str) -> str:
    return hashlib.sha1("|".join(p.lower().strip() for p in parts).encode()).hexdigest()[:16]


# ── SerpAPI · Google Jobs ────────────────────────────────────────────────
def fetch_serpapi(queries: list[str]) -> list[dict]:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        log.warning("SERPAPI_KEY not set — skipping Google Jobs (your main source!)")
        return []
    out: list[dict] = []
    for q in queries:
        next_token, pages = None, 0
        while pages < 1:  # capped at 1 page/query to stay within the 250-search/mo free tier
            params = {"engine": "google_jobs", "q": q, "hl": "en", "gl": "us", "api_key": key}
            if next_token:
                params["next_page_token"] = next_token
            try:
                r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                log.error("SerpAPI error for %r: %s", q, e)
                break
            for j in data.get("jobs_results", []):
                apply_url = ""
                for opt in j.get("apply_options", []):
                    apply_url = opt.get("link", "")
                    if apply_url:
                        break
                apply_url = apply_url or j.get("share_link", "")
                out.append({
                    "source": "google_jobs",
                    "source_id": _rid(j.get("company_name", ""), j.get("title", ""), j.get("location", "")),
                    "title": j.get("title", ""),
                    "employer": j.get("company_name", ""),
                    "location": j.get("location", ""),
                    "description": (j.get("description") or "")[:6000],
                    "url": apply_url,
                    "posted_hint": (j.get("detected_extensions") or {}).get("posted_at", ""),
                })
            next_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            pages += 1
            if not next_token:
                break
            time.sleep(1)
    log.info("SerpAPI: %d raw listings", len(out))
    return out


# ── Bright Data SERP API · Google jobs unit ──────────────────────────────
US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]


def fetch_brightdata(queries: list[str], sweep_query: str | None = None) -> list[dict]:
    """Google's jobs unit parsed from a plain SERP via Bright Data.
    The dedicated Google Jobs page (ibp=htl;jobs / udm=8) stopped parsing
    across providers in July 2026; the jobs unit embedded in regular search
    results still yields ~3 structured listings per query. The jobs unit is
    location-sensitive, so sweep_query additionally runs per US state to
    surface local listings a national query misses."""
    key = os.environ.get("BRIGHTDATA_KEY")
    zone = os.environ.get("BRIGHTDATA_ZONE", "surgeon_jobs")
    if not key:
        log.info("BRIGHTDATA_KEY not set — skipping Bright Data Google Jobs")
        return []
    search_terms = [q + " jobs" for q in queries]
    if sweep_query:
        search_terms += [f"{sweep_query} jobs in {s}" for s in US_STATES]
    out: list[dict] = []
    for q in search_terms:
        url = "https://www.google.com/search?q=" + requests.utils.quote(q) + "&hl=en&gl=us"
        data = None
        for attempt in range(3):  # transient 502s while the zone warms are normal
            try:
                r = requests.post(
                    "https://api.brightdata.com/request",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"zone": zone, "url": url, "format": "json", "data_format": "parsed"},
                    timeout=90,
                )
                r.raise_for_status()
                envelope = r.json()
                if envelope.get("status_code") != 200:
                    raise RuntimeError((envelope.get("headers") or {}).get("x-brd-error", "target non-200"))
                data = json.loads(envelope["body"])
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Bright Data attempt %d for %r: %s", attempt + 1, q, e)
                time.sleep(5)
        if data is None:
            log.error("Bright Data failed for %r after retries", q)
            continue
        for j in (data.get("jobs") or {}).get("items", []):
            desc_bits = [j.get("description") or ""]
            for h in j.get("highlights") or []:
                desc_bits.extend(h.get("list") or [])
            for t in j.get("tags") or []:
                if t.get("name") and t.get("value"):
                    desc_bits.append(f"{t['name']}: {t['value']}")
            out.append({
                "source": "google_jobs",
                "source_id": _rid(j.get("company", ""), j.get("title", ""), j.get("location", "")),
                "title": j.get("title", ""),
                "employer": j.get("company", ""),
                "location": j.get("location", ""),
                "description": " ".join(filter(None, desc_bits))[:6000],
                "url": j.get("apply_link") or j.get("link", ""),
                "posted_hint": j.get("date") or "",
            })
    log.info("Bright Data google jobs: %d raw listings", len(out))
    return out


# ── USAJobs (VA and other federal MIS/bariatric postings) ────────────────
def fetch_usajobs(keyword: str) -> list[dict]:
    key = os.environ.get("USAJOBS_KEY")
    email = os.environ.get("USAJOBS_EMAIL", "")
    if not key:
        log.info("USAJOBS_KEY not set — skipping USAJobs")
        return []
    try:
        r = requests.get(
            "https://data.usajobs.gov/api/search",
            params={"Keyword": keyword, "ResultsPerPage": 50},
            headers={"Host": "data.usajobs.gov", "User-Agent": email or UA, "Authorization-Key": key},
            timeout=30,
        )
        r.raise_for_status()
        items = r.json().get("SearchResult", {}).get("SearchResultItems", [])
    except Exception as e:  # noqa: BLE001
        log.error("USAJobs error: %s", e)
        return []
    out = []
    for it in items:
        d = it.get("MatchedObjectDescriptor", {})
        locs = d.get("PositionLocation", [{}])
        pay = (d.get("PositionRemuneration") or [{}])[0]
        desc = " ".join(filter(None, [
            (d.get("UserArea", {}).get("Details", {}) or {}).get("JobSummary", ""),
            f"Salary: {pay.get('MinimumRange', '')}-{pay.get('MaximumRange', '')} {pay.get('RateIntervalCode', '')}",
            f"Schedule: {d.get('PositionSchedule', [{}])[0].get('Name', '')}",
        ]))
        out.append({
            "source": "usajobs",
            "source_id": _rid("usajobs", d.get("PositionID", "")),
            "title": d.get("PositionTitle", ""),
            "employer": d.get("OrganizationName", ""),
            "location": locs[0].get("LocationName", "") if locs else "",
            "description": desc[:6000],
            "url": d.get("PositionURI", ""),
            "posted_hint": d.get("PublicationStartDate", ""),
        })
    log.info("USAJobs: %d raw listings", len(out))
    return out


# ── ASMBS Career Center (MemberSuite portal API) ─────────────────────────
ASMBS_ASSOC_ID = 36893  # ASMBS's association id on the MemberSuite platform
ASMBS_API = "https://express.portal.membersuite.com/prod"
ASMBS_JOB_URL = "https://asmbs.users.membersuite.com/community/career-center/job-details/{id}/job-detail"


def fetch_asmbs(url: str) -> list[dict]:
    """Niche board, highest-signal postings. The career center moved from
    jobs.asmbs.org to a MemberSuite SPA in 2026; instead of parsing HTML we
    use the portal's JSON API (anonymous token → search → GraphQL details).
    The `url` config value is kept for the log line / provenance only.
    """
    try:
        tok = requests.get(f"{ASMBS_API}/platform/getAnonymousUserToken/{ASMBS_ASSOC_ID}", timeout=30).json()
        auth = {"Authorization": "Bearer " + tok["data"]["authenticationData"]["accessToken"]}
        jobs = requests.post(f"{ASMBS_API}/careerCenter/jobPostings/search",
                             headers={**auth, "Content-Type": "application/json"},
                             json={}, timeout=30).json()
    except Exception as e:  # noqa: BLE001
        log.error("ASMBS fetch error: %s", e)
        return []
    out = []
    gql = ("query ($id: String!) { jobPostingDetails(id: $id) "
           "{ body name companyName city state postOn categories } }")
    for j in jobs[:25]:
        desc = ""
        try:
            d = requests.post("https://rest.membersuite.com/graphql/v2",
                              headers={**auth, "Content-Type": "application/json"},
                              json={"variables": {"id": j["id"]}, "query": gql},
                              timeout=30).json()
            details = (d.get("data") or {}).get("jobPostingDetails") or {}
            body = details.get("body") or ""
            if body:
                from bs4 import BeautifulSoup  # lazy import
                desc = " ".join(BeautifulSoup(body, "html.parser").get_text(" ").split())
            cats = details.get("categories")
            if cats:
                desc = f"Categories: {cats}. " + desc
        except Exception as e:  # noqa: BLE001
            log.warning("ASMBS details failed for %s: %s", j.get("id"), e)
        out.append({
            "source": "asmbs",
            "source_id": _rid("asmbs", j["id"]),
            "title": j.get("name", ""),
            "employer": j.get("companyName", ""),
            "location": ", ".join(filter(None, [j.get("city"), j.get("state")])),
            "description": desc[:6000],
            "url": ASMBS_JOB_URL.format(id=j["id"]),
            "posted_hint": (j.get("postOn") or "")[:10],
        })
    log.info("ASMBS: %d raw listings", len(out))
    return out


def fetch_all(cfg: dict) -> list[dict]:
    raw: list[dict] = []
    # SerpAPI free tier is 250 searches/mo — keep it to the first 4 queries
    raw += fetch_serpapi(cfg["search"]["queries"][:4])
    raw += fetch_brightdata(cfg["search"]["queries"], cfg["search"].get("brightdata_sweep_query"))
    raw += fetch_usajobs(cfg["search"]["usajobs_keyword"])
    raw += fetch_asmbs(cfg["search"]["asmbs_url"])
    # Cross-source dedupe on (employer, title, location) where present
    seen, deduped = set(), []
    for r in raw:
        k = _rid(r["employer"] or r["url"], r["title"], r["location"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    log.info("Total raw after cross-source dedupe: %d", len(deduped))
    return deduped
