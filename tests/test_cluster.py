"""One job, one card.

The real-world groupings here are taken verbatim from data/listings.json on
2026-09-20, where one Lenexa opening occupied seven rows. The NOT-the-same-job
cases matter more than the merges: a wrong merge hides a job from someone who
is looking for one.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import cluster  # noqa: E402


def L(employer, title, city="Springfield", state="XX", **kw):
    base = {"employer": employer, "title": title, "city": city, "state": state,
            "first_seen": "2026-09-01", "last_seen": "2026-09-20", "archived": False,
            "key": f"{employer}::{state}::{title}"}
    base.update(kw)
    return base


class SameJob(unittest.TestCase):
    def test_employer_spelled_five_ways_is_one_job(self):
        names = ["AdventHealth", "AdventHealth Kansas", "AdventHealth Kansas City",
                 "AdventHealth Medical Group", "AdventHealth Medical Group - Kansas"]
        rows = [L(n, "Bariatric & Metabolic Surgeon", "Lenexa", "KS") for n in names]
        self.assertEqual(len(cluster.group(rows)), 1)

    def test_title_drift_at_one_employer_is_one_job(self):
        rows = [L("AdventHealth", t, "Lenexa", "KS") for t in
                ("Bariatric Surgeon", "Bariatric & Metabolic Surgeon",
                 "Elite Bariatric & Metabolic Surgeon")]
        self.assertEqual(len(cluster.group(rows)), 1)

    def test_recruiter_and_hospital_naming_the_same_job(self):
        rows = [L("Archway Physician Recruitment", "Bariatric & MIS Surgeon", "Rapid City", "SD"),
                L("Rapid City Regional Hospital (via Archway Physician Recruitment)",
                  "Bariatric & MIS Surgeon", "Rapid City", "SD")]
        self.assertEqual(len(cluster.group(rows)), 1)

    def test_corporate_suffix_only(self):
        rows = [L("Hayman Daugherty Associates", "Bariatric Surgeon", "East Stroudsburg", "PA"),
                L("Hayman Daugherty Associates, Inc", "Bariatric Surgeon", "East Stroudsburg", "PA")]
        self.assertEqual(len(cluster.group(rows)), 1)

    def test_null_city_on_both_sides(self):
        rows = [L("Southern Illinois Health System", "Bariatric & General Surgeon", None, "IL"),
                L("Southern Illinois Health System", "Bariatric Surgeon", None, "IL")]
        self.assertEqual(len(cluster.group(rows)), 1)


class NotTheSameJob(unittest.TestCase):
    """Every one of these would hide a real opening if it merged."""

    def test_different_specialty_at_one_hospital(self):
        rows = [L("Mercy Hospital", "Bariatric Surgeon"),
                L("Mercy Hospital", "Colorectal Surgeon")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_disjoint_roles_do_not_merge_on_a_shared_token(self):
        # {general, bariatric} vs {bariatric, mis}: neither contains the other
        rows = [L("Archway Physician Recruitment", "General Surgery/Bariatric Surgeon", "Rapid City", "SD"),
                L("Archway Physician Recruitment", "Bariatric & MIS Surgeon", "Rapid City", "SD")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_same_employer_different_city(self):
        rows = [L("Britt Medical Search", "General Surgeon - Bariatric & MIS", "Portland", "ME"),
                L("Britt Medical Search", "General Surgeon - Bariatric & MIS", "Lewiston", "ME")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_same_employer_name_different_state(self):
        rows = [L("Banner Health", "Bariatric Surgeon", "Tucson", "AZ"),
                L("Banner Health", "Bariatric Surgeon", "Tucson", "CO")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_two_hospitals_sharing_only_noise_words(self):
        # "Medical"/"Center"/"Health" must never be what makes a match
        rows = [L("Mercy Medical Center", "Bariatric Surgeon"),
                L("Sutter Medical Center", "Bariatric Surgeon")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_employer_of_only_noise_words_never_matches(self):
        rows = [L("Medical Center", "Bariatric Surgeon"),
                L("Health System", "Bariatric Surgeon")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_title_with_no_recognised_role_never_matches(self):
        rows = [L("Mercy Hospital", "Physician"), L("Mercy Hospital", "Physician")]
        self.assertEqual(len(cluster.group(rows)), 2)


class RuleHardening(unittest.TestCase):
    """Cases a fresh-context review found in, or adjacent to, the real store."""

    def test_possessive_hospitals_are_not_the_same_hospital(self):
        # Naive tokenising leaves a bare "s" and "st", which alone clear the
        # overlap threshold and merge two unrelated hospitals.
        rows = [L("St. Luke's Hospital", "Bariatric Surgeon"),
                L("St. Mary's Hospital", "Bariatric Surgeon")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_tokens_shorter_than_three_characters_never_identify(self):
        self.assertNotIn("st", cluster.org_tokens("Ascension Medical Group St. Vincent"))
        self.assertNotIn("s", cluster.org_tokens("Doctor's Choice Placement"))
        self.assertIn("doctors", cluster.org_tokens("Doctor's Choice Placement"))

    def test_confidential_is_not_an_identity(self):
        # 27 listings say "Confidential". Two of them in one city are two
        # employers declining to be named, not one job.
        self.assertEqual(cluster.org_tokens("Confidential"), set())
        rows = [L("Confidential", "Bariatric Surgeon", "Phoenix", "AZ"),
                L("Confidential", "Bariatric Surgeon", "Phoenix", "AZ")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_a_bare_general_title_does_not_subsume_a_specific_one(self):
        rows = [L("Mercy Hospital", "General Surgeon"),
                L("Mercy Hospital", "General Surgery with Trauma & Bariatric Support")]
        self.assertEqual(len(cluster.group(rows)), 2)

    def test_specific_signatures_still_subset_match(self):
        rows = [L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS"),
                L("AdventHealth", "Bariatric & Metabolic Surgeon", "Lenexa", "KS")]
        self.assertEqual(len(cluster.group(rows)), 1)

    def test_a_card_never_chains_two_postings_that_do_not_match(self):
        # A and C are different roles; B matches both. Transitive grouping
        # would put all three on one card.
        a = L("Mercy Hospital", "Bariatric Surgeon")
        b = L("Mercy Hospital", "Bariatric & Colorectal Surgeon")
        c = L("Mercy Hospital", "Colorectal Surgeon")
        self.assertFalse(cluster.same_job(a, c))
        groups = cluster.group([a, b, c])
        for g in groups:
            for x in g:
                for y in g:
                    self.assertTrue(cluster.same_job(x, y), (x["title"], y["title"]))

    def test_grouping_does_not_depend_on_input_order(self):
        import random
        rows = [L(n, "Bariatric & Metabolic Surgeon", "Lenexa", "KS") for n in
                ("AdventHealth", "AdventHealth Kansas", "AdventHealth Medical Group")]
        rows.append(L("Mercy Hospital", "Colorectal Surgeon", "Lenexa", "KS"))
        shapes = set()
        for _ in range(8):
            random.shuffle(rows)
            shapes.add(tuple(sorted(tuple(sorted(m["key"] for m in g))
                                    for g in cluster.group(rows))))
        self.assertEqual(len(shapes), 1)


class CardContents(unittest.TestCase):
    def setUp(self):
        self.rows = [
            L("Spencer Britt", "General Surgeon - Bariatric Focus", "Lewiston", "ME",
              first_seen="2026-07-14", comp_min=350000, comp_max=450000, summary="s",
              call_burden="1:5"),
            L("Lewiston Hospital (via Britt Medical Search)", "General Surgeon - Bariatric & MIS",
              "Lewiston", "ME", first_seen="2026-07-11", robotics_mentioned=True),
            L("Britt Medical Search", "General Surgeon - Bariatric & MIS", "Lewiston", "ME",
              first_seen="2026-07-30", archived=True, mbsaqip_mentioned=True),
        ]
        self.card = cluster.build_card(self.rows)

    def test_days_on_market_uses_the_earliest_posting(self):
        self.assertEqual(self.card["first_seen"], "2026-07-11")

    def test_representative_names_an_institution_not_a_recruiter_person(self):
        self.assertEqual(self.card["employer"], "Lewiston Hospital (via Britt Medical Search)")

    def test_title_is_the_one_most_sources_used(self):
        self.assertEqual(self.card["title"], "General Surgeon - Bariatric & MIS")

    def test_capability_flags_are_unioned(self):
        self.assertTrue(self.card["robotics_mentioned"])
        self.assertTrue(self.card["mbsaqip_mentioned"])

    def test_comp_comes_from_whichever_posting_disclosed_it(self):
        self.assertEqual((self.card["comp_min"], self.card["comp_max"]), (350000, 450000))

    def test_card_is_active_while_any_posting_is(self):
        self.assertFalse(self.card["archived"])

    def test_every_posting_is_kept_and_marked(self):
        self.assertEqual(len(self.card["postings"]), 3)
        self.assertEqual(sum(1 for p in self.card["postings"] if p["archived"]), 1)

    def test_identity_is_the_earliest_posting_not_the_representative(self):
        # The representative is whichever posting reads best TODAY. If the
        # card's key followed it, a better posting arriving tomorrow would
        # re-key the card and orphan her saved "Applied" status.
        self.assertEqual(self.card["employer"], "Lewiston Hospital (via Britt Medical Search)")
        earliest = min(self.rows, key=lambda r: r["first_seen"])
        self.assertEqual(self.card["key"], earliest["key"])

    def test_identity_survives_a_new_better_posting_arriving(self):
        before = cluster.build_card(self.rows)["key"]
        newcomer = L("Central Maine Medical Center", "General Surgeon - Bariatric & MIS",
                     "Lewiston", "ME", first_seen="2026-09-19", comp_min=400000,
                     comp_max=500000, summary="s", call_burden="1:6")
        after = cluster.build_card(self.rows + [newcomer])
        self.assertEqual(after["key"], before)
        self.assertEqual(after["employer"], "Central Maine Medical Center")  # rep did change

    def test_a_card_of_one_is_just_the_listing(self):
        card = cluster.build_card([self.rows[0]])
        self.assertEqual(card["employer"], "Spencer Britt")
        self.assertEqual(len(card["postings"]), 1)


class DigestSuppression(unittest.TestCase):
    def _db(self, rows):
        return {"listings": {r["key"]: {k: v for k, v in r.items() if k != "key"} for r in rows}}

    def test_a_rewording_of_a_job_already_shown_is_not_announced(self):
        old = L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS", first_seen="2026-08-21")
        new = L("AdventHealth Medical Group", "Bariatric & Metabolic Surgeon", "Lenexa", "KS",
                first_seen="2026-09-20")
        self.assertFalse(cluster.is_new_job(self._db([old, new]), new))

    def test_a_genuinely_new_job_is_announced(self):
        old = L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS", first_seen="2026-08-21")
        new = L("Mercy Hospital", "Bariatric Surgeon", "Tulsa", "OK", first_seen="2026-09-20")
        self.assertTrue(cluster.is_new_job(self._db([old, new]), new))

    def test_every_wording_arriving_on_one_night_announces_once(self):
        rows = [L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS", first_seen="2026-09-20"),
                L("AdventHealth Kansas", "Bariatric Surgeon", "Lenexa", "KS", first_seen="2026-09-20"),
                L("AdventHealth Medical Group", "Bariatric Surgeon", "Lenexa", "KS",
                  first_seen="2026-09-20")]
        db = self._db(rows)
        self.assertEqual(sum(1 for r in rows if cluster.is_new_job(db, r)), 1)

    def test_an_already_announced_job_is_never_announced_again(self):
        # store.merge backdates first_seen to the posting date, so a wording
        # arriving tonight can look OLDER than the twin already emailed.
        old = L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS",
                first_seen="2026-09-15", announced=True)
        backdated = L("AdventHealth Kansas", "Bariatric Surgeon", "Lenexa", "KS",
                      first_seen="2026-09-10")
        self.assertFalse(cluster.is_new_job(self._db([old, backdated]), backdated))

    def test_mark_announced_covers_every_wording(self):
        rows = [L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS"),
                L("AdventHealth Kansas", "Bariatric Surgeon", "Lenexa", "KS"),
                L("Mercy Hospital", "Colorectal Surgeon", "Tulsa", "OK")]
        db = self._db(rows)
        cluster.mark_announced(db, [rows[0]])
        self.assertTrue(db["listings"][rows[0]["key"]]["announced"])
        self.assertTrue(db["listings"][rows[1]["key"]]["announced"])
        self.assertNotIn("announced", db["listings"][rows[2]["key"]])

    def test_an_archived_twin_does_not_suppress(self):
        old = L("AdventHealth", "Bariatric Surgeon", "Lenexa", "KS",
                first_seen="2026-01-01", archived=True)
        new = L("AdventHealth Medical Group", "Bariatric Surgeon", "Lenexa", "KS",
                first_seen="2026-09-20")
        self.assertTrue(cluster.is_new_job(self._db([old, new]), new))


class AgainstTheRealStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = json.loads((Path(__file__).resolve().parent.parent / "data" / "listings.json")
                       .read_text(encoding="utf-8"))["listings"]
        cls.listings = [{**v, "key": k} for k, v in d.items()]
        cls.cards = cluster.cards(cls.listings)

    def test_every_posting_lands_on_exactly_one_card(self):
        keys = [p["key"] for c in self.cards for p in c["postings"]]
        self.assertEqual(len(keys), len(self.listings))
        self.assertEqual(len(set(keys)), len(self.listings))

    def test_no_card_claims_to_be_newer_than_its_postings(self):
        bad = [c["key"] for c in self.cards
               if c["first_seen"] > min(p["first_seen"] for p in c["postings"])]
        self.assertEqual(bad, [])

    def test_no_card_holds_two_postings_that_do_not_match(self):
        import itertools
        bad = []
        for c in self.cards:
            members = [l for l in self.listings if l["key"] in {p["key"] for p in c["postings"]}]
            for x, y in itertools.combinations(members, 2):
                if not cluster.same_job(x, y):
                    bad.append((c["key"], x["title"], y["title"]))
        self.assertEqual(bad, [])

    def test_no_confidential_posting_was_merged_into_anything(self):
        bad = [c["key"] for c in self.cards if len(c["postings"]) > 1
               and any("confidential" in (p["employer"] or "").lower() for p in c["postings"])]
        self.assertEqual(bad, [])

    def test_clustering_actually_collapses_the_known_duplicates(self):
        active = [c for c in self.cards if not c["archived"]]
        merged = [c for c in active if len(c["postings"]) > 1]
        self.assertGreater(len(merged), 10)
        self.assertLess(len(active), len([l for l in self.listings if not l.get("archived")]))

    def test_the_adventhealth_lenexa_job_is_one_card(self):
        hits = [c for c in self.cards
                if c.get("city") == "Lenexa" and "bariatric" in (c.get("title") or "").lower()]
        self.assertEqual(len(hits), 1, [c["employer"] for c in hits])
        self.assertGreaterEqual(len(hits[0]["postings"]), 5)

    def test_card_identity_is_one_of_its_own_postings(self):
        bad = [c["key"] for c in self.cards
               if c["key"] not in {p["key"] for p in c["postings"]}]
        self.assertEqual(bad, [])

    def test_card_identities_are_unique(self):
        keys = [c["key"] for c in self.cards]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_active_card_still_has_an_apply_link(self):
        missing = [c["employer"] for c in self.cards if not c["archived"]
                   and not any(u.startswith("http") for u in (c.get("urls") or []))]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
