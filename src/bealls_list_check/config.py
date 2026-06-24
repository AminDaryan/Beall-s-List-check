"""Configuration for the Beall's List predatory-venue check.

Paths and tunables live here so the matcher and the Excel writer stay focused
on logic.  The corpus folder lives outside the repo, so it is overridable via
the ``BEALLS_CORPUS_DIR`` environment variable.
"""

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------

# Folder holding the Semantic Scholar dump JSON files (one list per file).
# Defaults to the in-repo docs folder; override with BEALLS_CORPUS_DIR if the
# data lives elsewhere.  _HERE.parent.parent is the repo root
# ("Paper repository scraping Code").
_DEFAULT_CORPUS_DIR = _HERE.parent.parent / "docs" / "Updated Abstract Papers"
CORPUS_DIR = Path(os.environ.get("BEALLS_CORPUS_DIR", str(_DEFAULT_CORPUS_DIR)))

# Glob for the corpus files inside CORPUS_DIR.
CORPUS_GLOB = "*.json"

# Vendored Beall's List snapshot produced by scrape_bealls_list.py.
SNAPSHOT_PATH = _HERE / "data" / "bealls_snapshot.json"

# Where the combined results workbook is written.
RESULTS_DIR = _HERE / "results"
RESULTS_FILENAME = "bealls_list_results.xlsx"

# ---------------------------------------------------------------------------
# Matching tunables
# ---------------------------------------------------------------------------

# difflib ratio threshold for the fuzzy "review" tier.  Deliberately high:
# in this corpus genuinely predatory venues are rare, so a loose threshold
# would surface far more false positives than true hits.
FUZZY_CUTOFF = 0.93

# Minimum normalized-name length to even attempt an exact-name match, so we
# never match on a stub like "ai" or "data".
MIN_NAME_LEN = 5

# Lists that the fuzzy tier is allowed to match against.  Fuzzy matching the
# low-relevance metric/vanity lists produces noise, so those are exact-only.
# "blacklist" = venues the *user* added (see user_lists.py); treated as a core,
# high-confidence list (asserts on_list, eligible for fuzzy near-name matching).
FUZZY_LIST_SOURCES = {"publishers", "standalone_journal", "hijacked", "blacklist"}

# "Weak" lists that should never assert a high-confidence "on_list" verdict for
# a journal article:
#   * vanity_press     — book/monograph publishers (e.g. IGI Global). Their
#                        journals are a different thing; flagging an article
#                        because the publisher self-publishes books is a
#                        category error.
#   * misleading_metric— companies selling fake impact-factor metrics; not
#                        venues at all, so a paper can't be published "in" one.
# A match on these is downgraded from on_list -> review (surfaced, not asserted).
WEAK_LIST_SOURCES = {"vanity_press", "misleading_metric"}

# Generic hosting / platform domains that some Beall entries live on (e.g. a
# predatory journal whose only web presence is a Google Site).  These are too
# broad to use as a domain-match key: matching a paper purely because its venue
# is "sites.google.com" would be meaningless.  Excluded from the domain index
# (the entry can still match by name).  Specific *sub*domains of a platform
# (e.g. "ijddmr.wordpress.com") are precise enough and are kept.
GENERIC_HOSTS = {
    "sites.google.com", "google.com", "docs.google.com", "drive.google.com",
    "groups.google.com", "github.io", "wordpress.com", "blogspot.com",
    "wixsite.com", "wix.com", "weebly.com", "webs.com",
    # National / regional academic aggregators that host thousands of
    # *legitimate* journals.  A single listed predatory journal on one of these
    # would otherwise poison the whole platform and flag every co-hosted journal
    # as on_list (e.g. one predatory journal on DergiPark flagging all Turkish
    # university journals).  Excluded from the domain index; a genuinely-listed
    # journal on them can still match by name.
    "dergipark.gov.tr", "dergipark.org.tr",   # DergiPark — Turkey's national platform
    "jstage.jst.go.jp",                         # J-STAGE — Japan's national platform
    "scielo.br",                                # SciELO Brazil open-access platform
}

# Registrable domains that are archives / preservation / persistent-identifier
# resolvers / catalogs — NOT publishers.  Semantic Scholar routinely stores a
# journal's national-library archive copy or a resolver link alongside the real
# publisher URL (e.g. MDPI's *Cancers* carries e-helvetica.nb.admin.ch +
# nbn-resolving.de next to mdpi.com).  Used by the data-quality "merged venue"
# check (to not count them as separate publishers) and by the matcher (to
# recognise that an alternate-URL match is the real venue when the canonical is
# one of these).
ARCHIVE_RESOLVER_DOMAINS = {
    "admin.ch",                                 # e-helvetica — Swiss National Library e-archive
    "nbn-resolving.de", "nbn-resolving.org",    # German NL URN/NBN resolver
    "d-nb.info",                                # Deutsche Nationalbibliothek
    "jstor.org",                                # JSTOR archive
    "portico.org",                              # Portico digital preservation
    "handle.net",                               # Handle persistent-identifier resolver
    "doi.org",                                  # DOI resolver
    "worldcat.org",                             # OCLC union catalog
    "nih.gov",                                  # PubMed / PMC (NCBI) — index, not a publisher
    "gale.com",                                 # Gale (Cengage) database
    "crossref.org",                             # Crossref registry
    "openalex.org",                             # OpenAlex index
    "base-search.net",                          # BASE search index
}

# Substrings that mark a host as an institutional repository / archive rather
# than a publisher (e.g. epub.uni-regensburg.de).  Used only to phrase an
# alternate-URL match: if the canonical venue is one of these, the alternate
# (the listed publisher) is most likely the paper's real venue.
REPOSITORY_HOST_MARKERS = (
    "epub", "eprints", "repository", "repositorio", "dspace", "openrepository",
)

# Substrings naming AGGREGATORS / indexes / databases / reference sites that list
# journals but don't PUBLISH them.  Like the archive domains, they are not a
# publisher, so the data-quality "merged venue" check ignores them when counting
# how many publishers a venue record points at (e.g. a journal listed on Gale's
# InfoTrac database next to its real publisher isn't a "merge").
AGGREGATOR_HOST_MARKERS = (
    "redalyc", "scielo", "latindex", "redib", "dialnet", "doaj",   # OA journal aggregators
    "galegroup", "proquest", "ebscohost",                          # commercial databases
    "wikipedia", "wikidata", "wikicfp",                            # reference / CFP listings
    "fatcat", "dblp", "semanticscholar", "researchgate", "academia.edu",
)

# Different web domains owned by the SAME publisher.  Collapsed to one before the
# "merged venue" check tallies publishers, so a journal listed under, e.g., both
# elsevier.com and sciencedirect.com isn't miscounted as "2 different publishers".
SAME_PUBLISHER_DOMAINS = {
    "sciencedirect.com": "elsevier.com",     # ScienceDirect is Elsevier
    "computer.org":       "ieee.org",        # IEEE Computer Society is IEEE
    "biomedcentral.com":  "springer.com",    # BMC is Springer Nature
    "springeropen.com":   "springer.com",    # SpringerOpen is Springer Nature
    "plosone.org":        "plos.org",        # PLOS ONE is PLOS
}

# Preprint-server hosts: a paper whose venue is one of these has no journal
# publisher and therefore cannot be "on Beall's List" — it is classified
# "no_venue" rather than clean/predatory.
PREPRINT_DOMAINS = {
    "arxiv.org", "biorxiv.org", "medrxiv.org", "chemrxiv.org",
    "preprints.org", "researchsquare.com", "ssrn.com", "techrxiv.org",
    "authorea.com", "osf.io", "hal.science", "hal.archives-ouvertes.fr",
    "eccc.weizmann.ac.il", "openreview.net",
}

# Normalized venue names that signal a preprint when no domain is available.
PREPRINT_NAMES = {
    "arxiv", "arxiv org", "biorxiv", "medrxiv", "chemrxiv", "ssrn",
    "research square", "researchsquare", "preprints", "preprints org",
    "techrxiv", "authorea", "openreview", "hal",
}

# ---------------------------------------------------------------------------
# Result statuses + Excel row colors
# ---------------------------------------------------------------------------
# NOTE: unlike the other checks in this repo (where green "yes" = good), a
# *match* here is a negative signal, so the color scheme is inverted:
#   on_list  red    — venue matches Beall's List by a high-confidence signal
#   review   orange — fuzzy / near match; a human should verify
#   clean    green  — venue identified and not on the list
#   no_venue grey   — preprint or no venue info; not classifiable
#   error    dark   — record could not be processed
# out_of_scope blue — paper's venue isn't in the user's whitelist, so it was
#                      skipped (only ever used when a whitelist is supplied).
STATUS_FILL_COLORS = {
    "on_list":      "FFC7CE",
    "review":       "FFD79A",
    "clean":        "C6EFCE",
    "no_venue":     "D9D9D9",
    "out_of_scope": "BDD7EE",
    "error":        "C00000",
}
