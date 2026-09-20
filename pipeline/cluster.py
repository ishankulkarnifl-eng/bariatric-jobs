"""One job, one card.

The store keys a listing on employer+state+title, which is an exact string
match. Every aggregator writes the employer differently, so one Lenexa job
arrived as "AdventHealth", "AdventHealth Kansas", "AdventHealth Kansas City",
"AdventHealth Medical Group" and "AdventHealth Medical Group - Kansas", and
occupied seven rows on the dashboard. Titles drift the same way, from
"Bariatric Surgeon" to "Elite Bariatric & Metabolic Surgeon".

Clustering happens HERE, at presentation time, and never in the store. The
store stays a faithful record of what each source actually said: nothing is
merged away, first_seen per posting is preserved, and if this rule gets a
grouping wrong the fix is a code change, not a data recovery. A card shows
its member count and can be expanded to every underlying posting, so a wrong
merge costs a click rather than a missed job.
"""
from __future__ import annotations

import re
from collections import Counter

# Words that say nothing about WHICH organisation this is.
ORG_NOISE = {
    "the", "a", "of", "and", "at", "for", "in", "inc", "llc", "ltd", "llp", "pc", "pa",
    "pllc", "corp", "co", "medical", "medicine", "health", "healthcare", "system",
    "systems", "group", "center", "centre", "hospital", "hospitals", "clinic", "clinics",
    "university", "school", "college", "division", "department", "dept", "associates",
    "association", "partners", "physician", "physicians", "recruitment", "recruiting",
    "recruiters", "search", "staffing", "solutions", "services", "care", "network",
    "regional", "community", "national", "institute", "foundation", "via",
}

# The tokens that say what the job IS. Everything else in a title is noise:
# marketing ("Elite"), the posting site's own embellishments, or punctuation.
ROLE_VOCAB = {
    "bariatric", "bariatrics", "metabolic", "mis", "minimally", "invasive", "laparoscopic",
    "foregut", "robotic", "robotics", "general", "colorectal", "trauma", "acute", "hernia",
    "abdominal", "wall", "endoscopic", "oncology", "breast", "vascular", "thoracic",
    "transplant", "pediatric", "hepatobiliary", "endocrine",
}
ROLE_ALIAS = {
    "bariatrics": "bariatric", "robotics": "robotic",
    "minimally": "mis", "invasive": "mis", "laparoscopic": "mis",
}


def _toks(s: str) -> list[str]:
    """Tokens of at least three characters, possessives kept whole.

    Splitting "St. Luke's" naively yields a bare "s" and "st", and those two
    tokens alone clear the org-overlap threshold against "St. Mary's" - two
    different hospitals, merged on punctuation. Folding the apostrophe gives
    {lukes} and {marys}, which is the distinction that actually matters.
    """
    folded = re.sub(r"['’]", "", (s or "").lower())
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", folded).split() if len(t) > 2]


# An employer field that is a placeholder, not a name. 27 listings say
# "Confidential"; two confidential bariatric postings in one city are not
# evidence of one job, they are evidence of two employers who both declined
# to be named.
NON_IDENTIFYING = {"confidential", "undisclosed", "anonymous", "unnamed", "employer",
                   "client", "unspecified", "private"}


def org_tokens(employer: str) -> set[str]:
    """The tokens that identify the organisation, noise words removed.
    Empty when the field names nobody, which never matches anything."""
    toks = {t for t in _toks(employer) if t not in ORG_NOISE and not t.isdigit()}
    return set() if toks <= NON_IDENTIFYING else toks


def role_signature(title: str) -> set[str]:
    """Which surgical role a title describes, as a normalised token set."""
    return {ROLE_ALIAS.get(t, t) for t in _toks(title) if t in ROLE_VOCAB}


def _same_place(a: dict, b: dict) -> bool:
    if a.get("state") != b.get("state"):
        return False
    return (a.get("city") or "").strip().lower() == (b.get("city") or "").strip().lower()


def _same_org(a: dict, b: dict) -> bool:
    ta, tb = org_tokens(a.get("employer")), org_tokens(b.get("employer"))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.6


def _compatible_role(a: dict, b: dict) -> bool:
    """One title's role must contain the other's. "Bariatric Surgeon" and
    "Bariatric & Metabolic Surgeon" are the same job; "Bariatric Surgeon" and
    "Colorectal Surgeon" are not, even at the same hospital."""
    sa, sb = role_signature(a.get("title")), role_signature(b.get("title"))
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    # A bare {general} is inside almost every other signature, so allowing it
    # to subset-match would fold a hospital's plain general-surgery opening
    # into its bariatric one. Anything more specific may still subset-match:
    # {bariatric} inside {bariatric, metabolic} is the same job worded twice.
    if {"general"} in (sa, sb):
        return False
    return sa <= sb or sb <= sa


def same_job(a: dict, b: dict) -> bool:
    return _same_place(a, b) and _same_org(a, b) and _compatible_role(a, b)


INSTITUTION_WORDS = {"hospital", "hospitals", "health", "healthcare", "medical", "medicine",
                     "clinic", "clinics", "university", "center", "centre", "system", "systems"}


def _informativeness(l: dict) -> tuple:
    """How much this posting actually tells you. Used to pick the member whose
    wording represents the cluster, so we never synthesise an employer name
    that no source ever published.

    Still-listed outranks everything: the representative supplies the card's
    employer, title, summary and — the part that matters — its apply link. An
    archived member is one no source has shown for stale_after_days, so its
    link is the one most likely to be dead. Measured 2026-09-20, ranking on
    informativeness alone put an archived posting at the head of 9 of the 111
    active cards, including the Eau Claire Vohra card, whose only live posting
    (first seen four days earlier) sat inside the <details> while the card
    pointed at a delisted jobmesh URL.

    Then naming an institution, which outranks field count: one Lewiston job
    was posted by "Lewiston Hospital (via Britt Medical Search)", by "Britt
    Medical Search", and by "Spencer Britt". The recruiter's name is not the
    employer.
    """
    names_org = bool(set(_toks(l.get("employer"))) & INSTITUTION_WORDS)
    filled = sum(1 for f in ("comp_min", "comp_max", "call_burden", "city", "summary")
                 if l.get(f) not in (None, "", False))
    return (bool(l.get("archived")), not names_org, -filled,
            l.get("first_seen") or "9999", l.get("key") or "")


def group(listings: list[dict]) -> list[list[dict]]:
    """Partition listings into groups that all describe the same job.

    Complete linkage: a posting joins a card only if it matches EVERY posting
    already on it. Transitive grouping would chain A to C through B even when
    same_job(A, C) is false, which is how a card ends up holding a bariatric
    role and a plain general-surgery role that share nothing. Measured on the
    2026-09-20 store the stricter rule costs two extra cards out of 111 and
    removes chaining entirely, which is the right trade when the failure mode
    is hiding an opening from someone who is looking for one.

    Sorted first so the partition is a function of the store's contents and
    not of dictionary order.
    """
    groups: list[list[dict]] = []
    for l in sorted(listings, key=lambda x: (x.get("key") or "")):
        for g in groups:
            if all(same_job(l, m) for m in g):
                g.append(l)
                break
        else:
            groups.append([l])
    return groups


def build_card(members: list[dict]) -> dict:
    """Collapse a group into the one record the dashboard renders.

    Everything a person reads comes verbatim from a real posting. The only
    computed fields are the aggregates: the earliest first_seen (so
    days-on-market reflects when the job actually appeared, not when this
    particular wording of it did) and the union of the capability flags.
    """
    members = sorted(members, key=_informativeness)
    rep = members[0]

    titles = Counter(m.get("title") for m in members if m.get("title"))
    title = titles.most_common(1)[0][0] if titles else rep.get("title")

    comp = next((m for m in members if m.get("comp_min") and m.get("comp_max")), None) \
        or next((m for m in members if m.get("comp_min") or m.get("comp_max")), rep)

    # Identity must NOT be the representative's key. The representative is
    # whichever posting is most informative today, and a better one arriving
    # tomorrow would silently re-key the card and orphan the status she saved
    # against it. The earliest posting is stable: nothing is ever deleted from
    # the store, only archived, and a new posting can never be older.
    anchor = min(members, key=lambda m: (m.get("first_seen") or "9999", m.get("key") or ""))

    return {
        **{f: rep.get(f) for f in ("employer", "city", "state", "employment_model",
                                   "summary", "source", "posted_date", "urls")},
        "key": anchor.get("key"),
        "title": title,
        "comp_min": comp.get("comp_min"),
        "comp_max": comp.get("comp_max"),
        "call_burden": next((m.get("call_burden") for m in members if m.get("call_burden")), None),
        "mbsaqip_mentioned": any(m.get("mbsaqip_mentioned") for m in members),
        "robotics_mentioned": any(m.get("robotics_mentioned") for m in members),
        "fellowship_required": any(m.get("fellowship_required") for m in members),
        "visa_sponsorship": any(m.get("visa_sponsorship") for m in members),
        # The whole point: one job that showed up five weeks ago under a
        # different employer spelling is five weeks on market, not one day.
        "first_seen": min(m["first_seen"] for m in members),
        "last_seen": max(m["last_seen"] for m in members),
        "archived": all(m.get("archived") for m in members),
        # Chronological, so the expanded card reads as the job's history
        # rather than in whatever order the representative sort produced.
        "postings": [
            {"key": m.get("key"), "employer": m.get("employer"), "title": m.get("title"),
             "source": m.get("source"), "first_seen": m.get("first_seen"),
             "archived": bool(m.get("archived")), "urls": m.get("urls") or []}
            for m in sorted(members, key=lambda m: (m.get("first_seen") or "", m.get("key") or ""))
        ],
    }


def cards(listings: list[dict]) -> list[dict]:
    """The dashboard's rows: one per job, not one per posting."""
    return [build_card(g) for g in group(listings)]


def is_new_job(db: dict, listing: dict) -> bool:
    """True when this listing is a job we have not shown before, rather than
    another aggregator's wording of one we already have. Keeps the digest
    from emailing the same job on five consecutive nights.

    The comparison is on (first_seen, key), not first_seen alone. Five
    aggregators can hand us one job on a single night — that is the normal
    case on a first run, and it happens whenever a posting goes wide — and on
    a bare first_seen < first_seen every member of that tie loses to nobody,
    so all five get emailed. Ordering by key as well makes exactly one member
    of a tie the earliest, so the job is announced once.
    """
    key = listing.get("key") or ""
    mine = (listing.get("first_seen", ""), key)
    for k, other in db["listings"].items():
        if k == key or not same_job(listing, {**other, "key": k}):
            continue
        # A recorded fact beats any inference from dates. store.merge
        # backdates first_seen to the posting date, so a wording arriving
        # tonight can look OLDER than the twin she was emailed last week.
        if other.get("announced"):
            return False
        if other.get("archived"):
            continue  # a stale twin does not suppress a genuinely fresh posting
        if (other.get("first_seen", ""), k) < mine:
            return False  # several wordings arrived together; one of them wins
    return True


def mark_announced(db: dict, listings: list[dict]) -> None:
    """Record that the digest covered these jobs, so no wording of them is
    ever emailed again."""
    for l in listings:
        for k, other in db["listings"].items():
            if same_job({**l, "key": l.get("key")}, {**other, "key": k}):
                other["announced"] = True
