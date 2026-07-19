"""Source adapters. Each returns a list of RawListing dicts:
{source, source_id, title, employer, location, description, url, posted_hint}

Design note: we deliberately use ONE paid aggregator (Google Jobs via SerpAPI)
instead of N bespoke scrapers. Google Jobs already crawls hospital ATS systems
(Workday, iCIMS, Greenhouse), Indeed, LinkedIn, and most physician boards.
ASMBS and USAJobs are added for niche/federal coverage the aggregator misses.
"""
from __future__ import annotations

import hashlib
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


# ── ASMBS Career Center (best-effort HTML parse) ─────────────────────────
def fetch_asmbs(url: str) -> list[dict]:
    """Niche board, highest-signal postings. Career-site platforms change
    markup periodically; this parser is intentionally loose (grabs job links)
    and the extractor does the heavy lifting from the linked description text.
    If it silently returns 0 for a while, check the selectors.
    """
    try:
        from bs4 import BeautifulSoup  # lazy import
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:  # noqa: BLE001
        log.error("ASMBS fetch error: %s", e)
        return []
    out, seen = [], set()
    for a in soup.select("a[href*='/job/']"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 8 or href in seen:
            continue
        seen.add(href)
        if href.startswith("/"):
            href = "https://jobs.asmbs.org" + href
        out.append({
            "source": "asmbs",
            "source_id": _rid("asmbs", href),
            "title": title,
            "employer": "",          # extractor pulls this from the page text
            "location": "",
            "description": _fetch_page_text(href),
            "url": href,
            "posted_hint": "",
        })
        if len(out) >= 25:
            break
    log.info("ASMBS: %d raw listings", len(out))
    return out


def _fetch_page_text(url: str) -> str:
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer"]):
            t.decompose()
        return " ".join(soup.get_text(" ").split())[:6000]
    except Exception:  # noqa: BLE001
        return ""


def fetch_all(cfg: dict) -> list[dict]:
    raw: list[dict] = []
    raw += fetch_serpapi(cfg["search"]["queries"])
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
