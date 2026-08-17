"""Tests for the multi-provider seam and the two paths it makes load-bearing.

A *provider* is one published catalogue of allegedly predatory venues. This file
pins the contract that lets a second one be added without changing how the first
behaves:

  * every entry is stamped with the catalogue it came from (``provider``), while
    ``list_source`` keeps meaning the KIND of listing;
  * a catalogue whose sub-list is not mapped onto a known category fails at
    LOAD, loudly, rather than silently losing the weak-list downgrade;
  * ``build_index``'s return shape and ``snapshot["meta"]``'s dict shape are
    unchanged, because three call sites depend on both;
  * the user's own blacklist can never be dropped by a provider selection.

It also covers two paths that were previously untested and that a second
catalogue would put under real load: the ISSN tier, and the DOI confirmation of
a name match.

Run:
    python -m unittest discover -s tests -t tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "src" / "bealls_list_check"
for _p in [_PKG, *(d for d in _PKG.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(_PKG.parent))

import bealls_list_check as det                       # noqa: E402
import config                                         # noqa: E402
import data_quality                                   # noqa: E402
import resolve as resolve_mod                         # noqa: E402
from match import BeallIndex, match_paper, _best      # noqa: E402
from user_lists import load_user_entries              # noqa: E402


def _entry(name, domain, list_source, provider=None, issns=()):
    e = {"name": name, "url": f"http://{domain}/" if domain else "",
         "domain": domain, "normalized_name": name.lower(),
         "issns": list(issns), "list_source": list_source}
    if provider is not None:
        e["provider"] = provider
    return e


def _paper(pid, venue, url=None, issn=None, doi=None):
    pv = {"name": venue, "type": "journal"}
    if url:
        pv["url"] = url
    if issn:
        pv["issn"] = issn
    return {"paperId": pid, "title": f"paper {pid}", "year": 2024,
            "externalIds": {"DOI": doi} if doi else {},
            "venue": venue, "publicationVenue": pv}


class _TempProvider:
    """Context manager registering an extra provider backed by a temp snapshot."""

    def __init__(self, pid, label, entries, meta=None):
        self.pid, self.label, self.entries = pid, label, entries
        self.meta = meta or {"scraped_utc": "2026-02-02T00:00:00+00:00",
                             "total_entries": len(entries), "source": f"https://{pid}.test"}
        self._tmp = None
        self._saved = None

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="bealls_prov_")
        path = Path(self._tmp.name) / f"{self.pid}.json"
        path.write_text(json.dumps({"meta": self.meta, "entries": self.entries}),
                        encoding="utf-8")
        self._saved = dict(config.PROVIDERS)
        config.PROVIDERS[self.pid] = {"label": self.label, "snapshot": path,
                                      "home": f"https://{self.pid}.test", "vendored": False}
        det.PROVIDERS = config.PROVIDERS
        return self

    def __exit__(self, *exc):
        config.PROVIDERS.clear()
        config.PROVIDERS.update(self._saved)
        det.PROVIDERS = config.PROVIDERS
        self._tmp.cleanup()
        return False


class TestProviderStamping(unittest.TestCase):

    def test_beall_entries_are_stamped_with_their_provider(self):
        _index, snapshot, n_black = det.build_index()
        self.assertEqual(n_black, 0)
        providers = {e.get("provider") for e in snapshot["entries"]}
        self.assertEqual(providers, {"beall"},
                         "every entry from the shipped snapshot must be stamped 'beall'")

    def test_list_source_values_are_unchanged_by_stamping(self):
        """The category field must keep its five existing values — the weak-list
        downgrade, the fuzzy tier and the report labels are all keyed on them."""
        _index, snapshot, _n = det.build_index()
        seen = {e["list_source"] for e in snapshot["entries"]}
        self.assertEqual(seen, {"publishers", "standalone_journal", "hijacked",
                                "vanity_press", "misleading_metric"})
        self.assertTrue(seen <= config.KNOWN_LIST_SOURCES)

    def test_user_entries_are_stamped_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bl.json"
            p.write_text(json.dumps([{"name": "Mine", "url": "http://mine.test/"}]),
                         encoding="utf-8")
            entries = load_user_entries(str(p), "blacklist")
        self.assertEqual([e["provider"] for e in entries], ["user"])

    def test_default_is_beall_alone(self):
        self.assertEqual(tuple(config.DEFAULT_PROVIDERS), ("beall",))


class TestLoadTimeValidation(unittest.TestCase):

    def test_unmapped_category_fails_at_load(self):
        """The dangerous failure this prevents: an unknown category would miss
        WEAK_LIST_SOURCES and so be ASSERTED as a confident hit rather than
        downgraded to review — invisibly, on every paper."""
        bad = [_entry("Some Conference Series", "conf.test", "predatory_conference")]
        with _TempProvider("badprov", "Bad Provider", bad):
            with self.assertRaises(SystemExit) as ctx:
                det.build_index(providers=["badprov"])
        msg = str(ctx.exception)
        self.assertIn("predatory_conference", msg)
        self.assertIn("Bad Provider", msg)

    def test_unknown_provider_id_fails_at_load(self):
        with self.assertRaises(SystemExit) as ctx:
            det.build_index(providers=["no-such-list"])
        self.assertIn("no-such-list", str(ctx.exception))


class TestBuildIndexShape(unittest.TestCase):
    """ARCHITECTURE §5.7 — three call sites unpack this tuple and read these keys."""

    def test_return_shape_is_unchanged(self):
        result = det.build_index()
        self.assertEqual(len(result), 3)
        index, snapshot, n_black = result
        self.assertIsInstance(snapshot, dict)
        self.assertIn("entries", snapshot)
        self.assertIsInstance(snapshot["meta"], dict,
                              "meta must stay a dict — report.py calls .get() on it")
        self.assertIsInstance(n_black, int)

    def test_meta_answers_the_four_keys_the_summary_reads(self):
        _i, snapshot, _n = det.build_index()
        for key in ("scraped_utc", "total_entries", "source", "note"):
            self.assertIn(key, snapshot["meta"])

    def test_merged_meta_reports_the_oldest_date_and_every_provider(self):
        """A report is only as current as the stalest list behind it."""
        other = [_entry("Other Publishing", "other.test", "publishers")]
        with _TempProvider("other", "Other List", other,
                           meta={"scraped_utc": "2020-01-01T00:00:00+00:00",
                                 "total_entries": 1, "source": "https://other.test"}):
            _i, snapshot, _n = det.build_index(providers=["beall", "other"])
        self.assertEqual(snapshot["meta"]["scraped_utc"], "2020-01-01T00:00:00+00:00")
        listed = [p["provider"] for p in snapshot["meta"]["providers"]]
        self.assertEqual(listed, ["beall", "other"])

    def test_a_provider_selection_never_drops_the_user_blacklist(self):
        """The blacklist is concatenated after the provider entries, so no
        selection can disable BeallIndex.user_index and with it the whole
        blacklist tier."""
        other = [_entry("Other Publishing", "other.test", "publishers")]
        with tempfile.TemporaryDirectory() as tmp:
            bl = Path(tmp) / "bl.json"
            bl.write_text(json.dumps([{"name": "Mine", "url": "http://mine.test/"}]),
                          encoding="utf-8")
            with _TempProvider("other", "Other List", other):
                index, _snap, n_black = det.build_index(str(bl), providers=["other"])
        self.assertEqual(n_black, 1)
        self.assertIsNotNone(index.user_index,
                             "the blacklist tier must survive any provider selection")


class TestBestTieBreak(unittest.TestCase):

    def test_same_category_different_providers_is_deterministic(self):
        """Without a provider rank the winner depended on concatenation order."""
        a = _entry("Shared Name", "a.test", "publishers", provider="beall")
        b = _entry("Shared Name", "b.test", "publishers", provider="user")
        self.assertIs(_best([a, b]), b, "the user's own entry outranks a catalogue's")
        self.assertIs(_best([b, a]), b, "and the answer must not depend on order")


class TestIssnTier(unittest.TestCase):
    """The ISSN tier, previously untested against catalogue data.

    None of the 3,045 shipped Beall entries carries an ISSN, so this tier has
    only ever fired for the user's own entries. A catalogue that publishes ISSNs
    would activate it across thousands of entries at once, which is why its
    behaviour is pinned here BEFORE any such catalogue is enabled.
    """

    def test_shipped_beall_snapshot_carries_no_issns(self):
        _i, snapshot, _n = det.build_index()
        with_issn = [e for e in snapshot["entries"] if e.get("issns")]
        self.assertEqual(with_issn, [], "if this fails, the ISSN tier's blast radius changed")

    def test_issn_match_asserts_on_list(self):
        idx = BeallIndex([_entry("Listed Journal", "listed.test", "standalone_journal",
                                 provider="beall", issns=["1111-2222"])])
        r = match_paper(idx, _paper("p1", "Totally Different Name", issn="1111-2222"),
                        "f.json")
        self.assertEqual(r["status"], "on_list")
        self.assertEqual(r["matched_on"], "issn")
        self.assertEqual(r["provider"], "beall")

    def test_issn_match_needs_no_corroboration_unlike_a_name_match(self):
        """Documents the asymmetry deliberately, as a known risk.

        A name match against an entry that has a website is downgraded to review
        unless the paper's own site corroborates it. An ISSN match is asserted
        with no such check. That is defensible for a registered identifier, but it
        means enabling an ISSN-bearing catalogue asserts on_list on ISSN alone.
        """
        entry = _entry("Listed Journal", "listed.test", "standalone_journal",
                       provider="beall", issns=["1111-2222"])
        idx = BeallIndex([entry])

        by_issn = match_paper(idx, _paper("p1", "Unrelated", url="http://elsewhere.test/j",
                                          issn="1111-2222"), "f.json")
        by_name = match_paper(idx, _paper("p2", "Listed Journal",
                                          url="http://elsewhere.test/j"), "f.json")
        self.assertEqual(by_issn["status"], "on_list")   # asserted on the ISSN alone
        self.assertEqual(by_name["status"], "review")    # name needs the site to agree


class TestNameExactConfirmationUsesTheMatchedEntry(unittest.TestCase):
    """The DOI confirmation must check the entry that actually matched.

    It used to re-derive one with ``_best(index.by_name[...])``, which answers a
    different question: "the best entry with this title". In the shipped snapshot
    the two answers coincide — 0 of 3,045 entries have a stored normalized_name
    that differs from normalize_name(name) — so this was latent rather than a live
    misreport. A second catalogue is what makes them diverge: Kscien's titles
    carry replacement characters and non-breaking spaces, so a fetcher that
    normalises the name but keeps the raw title would send the lookup to a
    different bucket than the entry under review.
    """

    def setUp(self):
        self._saved_lookup = data_quality._crossref_lookup

    def tearDown(self):
        data_quality._crossref_lookup = self._saved_lookup

    def test_confirmation_names_the_entry_under_review(self):
        # Two entries whose titles normalise to the same key, so the name index
        # holds both; the one under review is NOT the one _best would pick.
        winner = _entry("Journal of Shared Titles", "winner.test", "publishers",
                        provider="user")
        under_review = _entry("Journal of Shared Titles", "review.test",
                              "standalone_journal", provider="beall",
                              issns=["3333-4444"])
        idx = BeallIndex([winner, under_review])

        r = {"status": "review", "matched_on": "name_exact",
             "matched_name": under_review["name"], "matched_url": under_review["url"],
             "list_source": under_review["list_source"], "provider": "beall",
             "matched_entry": under_review, "review_reason": "", "venue": "Journal of Shared Titles",
             "venue_domain": "", "issn": "", "authentic_url": "", "authentic_domain": "",
             "cleared_reason": "", "alt_source_urls": [], "canonical_domain": ""}
        paper = _paper("p1", "Journal of Shared Titles", url="http://elsewhere.test/j",
                       doi="10.1000/x")

        # Crossref registers the paper to the ISSN of the entry UNDER REVIEW.
        data_quality._crossref_lookup = lambda doi: {
            "ISSN": ["3333-4444"], "container-title": ["Journal of Shared Titles"],
            "publisher": "Review Test Press"}

        resolve_mod._resolve_name_exact(r, paper, idx)

        self.assertEqual(r["status"], "on_list")
        self.assertEqual(r["matched_on"], "name_exact_doi")
        self.assertEqual(r["matched_url"], under_review["url"],
                         "the confirmation must report the entry under review, "
                         "not a same-named entry from another catalogue")
        self.assertEqual(r["provider"], "beall")


if __name__ == "__main__":
    unittest.main()
