"""JSON store with the two fields no job board gives you: first_seen and
last_seen. Together they power days-on-market, the NEW badge, staleness
archiving, and repost detection — the highest-utility analytics in the app.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "data" / "listings.json"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def usable_url(url) -> bool:
    """A link is only worth storing if it works outside a Google SERP.
    sources.resolve_job_url returns "" for everything that does not."""
    return str(url or "").startswith(("http://", "https://"))


def apply_url(l: dict) -> str:
    """The link the dashboard and digest point at: first usable URL, if any.

    First, not last: measured on 2026-09-20 against the 21 active listings
    whose first and last URL are different hosts, the oldest link was live
    16/21 and the newest 15/21. There is no freshness advantage to trade the
    stability of a link that does not move for."""
    return next((u for u in (l.get("urls") or []) if usable_url(u)), "")


def needs_link(db: dict, l: dict) -> bool:
    """True when banking this listing would give us a link we do not have.
    Lets the orchestrator resolve only the handful of redirects that matter."""
    cur = db["listings"].get(listing_key(l))
    return not cur or not apply_url(cur)


def listing_key(l: dict) -> str:
    """Stable identity across sources and reposts: employer + state + title.
    A recruiter reposting the same job under a new URL maps to the same key,
    which is exactly what lets us count reposts instead of double-listing."""
    return f"{_slug(l.get('employer'))}::{l.get('state') or 'xx'}::{_slug(l.get('title'))}"


def load() -> dict:
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {"listings": {}, "meta": {"sample": False, "last_run": None}}


def save(db: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(db, indent=1, sort_keys=True), encoding="utf-8", newline="\n")


def merge(db: dict, extracted: list[dict], cfg: dict) -> list[dict]:
    """Merge today's extraction into the store. Returns listings that are
    genuinely NEW today (for the email digest)."""
    today = date.today().isoformat()
    new_today: list[dict] = []
    for l in extracted:
        k = listing_key(l)
        if k in db["listings"]:
            cur = db["listings"][k]
            cur["last_seen"] = today
            # New URL for a known job = repost (recruiter churn signal).
            # Only absolute links are banked: a relative Google redirect is a
            # dead link on the dashboard and mints a fresh token nightly,
            # which would read as an endless stream of reposts.
            if usable_url(l["url"]) and l["url"] not in cur["urls"]:
                # The FIRST usable link for a listing that had none is not a
                # repost, it is the link we were missing.
                if cur["urls"]:
                    cur["repost_count"] = cur.get("repost_count", 0) + 1
                cur["urls"].append(l["url"])
            # Comp disclosure can appear later; upgrade nulls only
            for f in ("comp_min", "comp_max", "call_burden"):
                if cur.get(f) is None and l.get(f) is not None:
                    cur[f] = l[f]
        else:
            # Backdate first_seen to the extracted posting date when known, so
            # days-on-market reflects true listing age, not pipeline launch day
            first_seen = today
            try:
                pd = date.fromisoformat(str(l.get("posted_date"))[:10])
                if pd <= date.today():
                    first_seen = pd.isoformat()
            except (ValueError, TypeError):
                pass
            db["listings"][k] = {
                **{f: l.get(f) for f in (
                    "employer", "title", "city", "state", "employment_model",
                    "comp_min", "comp_max", "call_burden", "mbsaqip_mentioned",
                    "robotics_mentioned", "fellowship_required", "visa_sponsorship",
                    "summary", "source", "posted_date",
                )},
                "urls": [l["url"]] if usable_url(l.get("url")) else [],
                "first_seen": first_seen,
                "last_seen": today,
                "repost_count": 0,
                "archived": False,
            }
            new_today.append(db["listings"][k])

    # Archive anything no source has shown us recently
    stale_cutoff = (date.today() - timedelta(days=cfg["store"]["stale_after_days"])).isoformat()
    for l in db["listings"].values():
        l["archived"] = l["last_seen"] < stale_cutoff

    db["meta"]["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "") + "Z"
    if extracted and db["meta"].get("sample"):
        # First real data: drop the fictional seed listings entirely
        real_keys = {listing_key(l) for l in extracted}
        db["listings"] = {k: v for k, v in db["listings"].items() if k in real_keys}
        db["meta"]["sample"] = False
    return new_today


def active(db: dict) -> list[dict]:
    return [l for l in db["listings"].values() if not l["archived"]]
