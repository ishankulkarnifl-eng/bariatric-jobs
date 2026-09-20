"""The apply-link path: the one thing on this dashboard that has to work.

Every case here is a failure mode that was live in production or was found in
review of the 2026-09-20 fix. Run with: python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import sources, store  # noqa: E402

REAL = "https://board.example.com/job/1"


def _resp(status, location=None):
    r = mock.Mock()
    r.status_code = status
    r.headers = {"Location": location} if location else {}
    return r


class ResolveJobUrl(unittest.TestCase):
    def setUp(self):
        sources._REDIRECT_CACHE.clear()
        for k in sources._RESOLVE_STATS:
            sources._RESOLVE_STATS[k] = 0
        p = mock.patch.object(sources.time, "sleep", lambda *_: None)
        p.start()
        self.addCleanup(p.stop)

    def _resolve(self, url, responses):
        with mock.patch.object(sources.requests, "get",
                               side_effect=responses or Exception("network not expected")):
            return sources.resolve_job_url(url)

    def test_relative_goto_resolves_to_the_employer(self):
        # The production bug: Google hands back its own relative redirect.
        self.assertEqual(self._resolve("/goto?url=TOK", [_resp(302, REAL)]), REAL)

    def test_failure_banks_nothing(self):
        # Never fall back to a google.com link: it changes nightly, which
        # inflates repost_count, and whichever we banked first would stick.
        self.assertEqual(self._resolve("/goto?url=TOK", [ConnectionError("rate limited")]), "")

    def test_consent_wall_is_not_a_destination(self):
        self.assertEqual(self._resolve("/goto?url=TOK", [_resp(302, "https://consent.google.com/m?c=1")]), "")

    def test_chained_google_hops_reach_the_destination(self):
        self.assertEqual(
            self._resolve("/goto?url=TOK", [_resp(302, "https://www.google.com/url?q=z"), _resp(302, REAL)]),
            REAL)

    def test_relative_location_header_is_joined(self):
        self.assertEqual(
            self._resolve("/goto?url=TOK", [_resp(302, "/url?q=y"), _resp(302, REAL)]), REAL)

    def test_no_location_header(self):
        self.assertEqual(self._resolve("/goto?url=TOK", [_resp(200)]), "")

    def test_hop_limit_stops_a_google_loop(self):
        self.assertEqual(
            self._resolve("/goto?url=TOK", [_resp(302, "https://www.google.com/url?q=a")] * 10), "")

    def test_employer_url_carrying_a_google_query_param_is_untouched(self):
        # Host matching must not be a substring test, or an ATS tracking link
        # with a google.com redirect parameter gets fetched and rewritten.
        u = "https://jobs.employer.com/a?next=https://www.google.com/url?q=x"
        self.assertEqual(self._resolve(u, []), u)

    def test_plain_employer_url_passes_through_without_a_request(self):
        self.assertEqual(self._resolve("https://jobs.employer.com/x", []), "https://jobs.employer.com/x")

    def test_google_search_share_link_is_not_an_apply_link(self):
        self.assertEqual(self._resolve("https://www.google.com/search?q=surgeon+jobs", []), "")

    def test_protocol_relative_url(self):
        self.assertEqual(self._resolve("//jobs.example.com/x", []), "")

    def test_non_http_schemes(self):
        for u in ("javascript:alert(1)", "mailto:a@b.c", "", "   ", "not a url"):
            self.assertEqual(self._resolve(u, []), "", u)

    def test_cache_avoids_a_second_fetch(self):
        with mock.patch.object(sources.requests, "get", side_effect=[_resp(302, REAL)]) as g:
            a = sources.resolve_job_url("/goto?url=C1")
            b = sources.resolve_job_url("/goto?url=C1")
        self.assertEqual((a, b), (REAL, REAL))
        self.assertEqual(g.call_count, 1)


class StoreLinkRules(unittest.TestCase):
    CFG = {"store": {"stale_after_days": 30}}

    def _listing(self, url):
        return {"employer": "Acme", "title": "Surgeon", "state": "CA", "url": url, "posted_date": None}

    def setUp(self):
        self.db = {"listings": {}, "meta": {"sample": False, "last_run": None}}

    def test_unusable_link_is_never_banked(self):
        store.merge(self.db, [self._listing("/goto?url=tok")], self.CFG)
        k = store.listing_key(self._listing(""))
        self.assertEqual(self.db["listings"][k]["urls"], [])

    def test_a_nightly_token_is_not_a_repost(self):
        store.merge(self.db, [self._listing("/goto?url=a")], self.CFG)
        store.merge(self.db, [self._listing("/goto?url=b")], self.CFG)
        k = store.listing_key(self._listing(""))
        self.assertEqual(self.db["listings"][k]["repost_count"], 0)

    def test_first_real_link_for_a_linkless_listing_is_not_a_repost(self):
        store.merge(self.db, [self._listing("/goto?url=a")], self.CFG)
        store.merge(self.db, [self._listing("https://acme.jobs/1")], self.CFG)
        k = store.listing_key(self._listing(""))
        self.assertEqual(self.db["listings"][k]["repost_count"], 0)
        self.assertEqual(self.db["listings"][k]["urls"], ["https://acme.jobs/1"])

    def test_a_genuinely_different_link_counts_once(self):
        for u in ("https://acme.jobs/1", "https://acme.jobs/2", "https://acme.jobs/2"):
            store.merge(self.db, [self._listing(u)], self.CFG)
        k = store.listing_key(self._listing(""))
        self.assertEqual(self.db["listings"][k]["repost_count"], 1)

    def test_apply_url_skips_unusable_entries(self):
        self.assertEqual(store.apply_url({"urls": ["/goto?url=x", "https://real/job"]}), "https://real/job")
        self.assertEqual(store.apply_url({"urls": ["/goto?url=x"]}), "")
        self.assertEqual(store.apply_url({}), "")

    def test_needs_link_only_for_listings_without_one(self):
        l = self._listing("https://acme.jobs/1")
        self.assertTrue(store.needs_link(self.db, l))
        store.merge(self.db, [l], self.CFG)
        self.assertFalse(store.needs_link(self.db, l))


class StoredDataInvariants(unittest.TestCase):
    """The committed store must agree with what merge() would produce."""

    @classmethod
    def setUpClass(cls):
        import json
        cls.listings = json.loads(
            (Path(__file__).resolve().parent.parent / "data" / "listings.json").read_text(encoding="utf-8")
        )["listings"]

    def test_every_stored_url_is_usable(self):
        bad = [u for v in self.listings.values() for u in (v.get("urls") or []) if not store.usable_url(u)]
        self.assertEqual(bad, [])

    def test_no_stored_url_points_back_at_google(self):
        from urllib.parse import urlsplit
        bad = [u for v in self.listings.values() for u in (v.get("urls") or [])
               if sources._is_google(urlsplit(u).netloc)]
        self.assertEqual(bad, [])

    def test_repost_count_matches_the_url_list(self):
        bad = {k: (v.get("repost_count"), len(v.get("urls") or []))
               for k, v in self.listings.items()
               if v.get("repost_count") != max(0, len(v.get("urls") or []) - 1)}
        self.assertEqual(bad, {})

    def test_every_active_listing_has_an_apply_link(self):
        missing = [k for k, v in self.listings.items() if not v.get("archived") and not store.apply_url(v)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
