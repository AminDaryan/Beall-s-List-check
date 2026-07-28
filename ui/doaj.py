"""The DOAJ journal list, held locally so a venue can be found while it is typed.

The Directory of Open Access Journals (https://doaj.org) is the reference index
of vetted open-access journals.  Step 3's **Only check these venues** list is a
*scope filter* — see ``user_lists.py`` — and typing a journal into it by hand
means knowing its exact title, its website, or its ISSN.  DOAJ knows all three,
so this module turns "the journal I mean is called roughly this" into an entry
the check can match on.

**Why the whole list, and not the search API.**  The box searches as the name is
typed, and a request per keystroke is both too slow and not ours to make: DOAJ is
a free service run by a non-profit, and typing four letters into its search
endpoint earns a Cloudflare 502 with ``Retry-After: 60`` — measured, not
supposed.  DOAJ publishes the entire journal list as one CSV for exactly this
reason, so it is fetched **once** (~23,000 journals, ~25 MB, a few seconds),
reduced to the five fields a venue entry needs, and cached on disk.  Everything
after that is a dictionary lookup: instant from the first character, correct
offline, and no load on DOAJ at all.

Nothing in the check engine imports this: DOAJ has no part in any verdict, and
must not acquire one.  Being indexed in DOAJ says a journal passed DOAJ's
editorial screening; it is not the question this tool asks, which is whether the
venue appears on Beall's List.  All this module does is help *name* a venue.

Like ``runners.py`` this is import-safe — no Streamlit — so it can be exercised
from a shell:

    >>> journals, err = load_journals()
    >>> search(journals, "power sources adv")[0].issns
    ('2666-2485',)
"""

import csv
import io
import json
import re
import time
from pathlib import Path

import requests

from venue_lookup import ISSN_RE, Venue, normalize_issn, site_is_shared  # noqa: F401

# The venue shape and the shared-host test live in venue_lookup, so this module
# and openalex.py hand back the same thing. Re-exported under the old name
# because a DOAJ record is always a journal.
Journal = Venue


# DOAJ's own "all journals as CSV" download — one request, refreshed daily at
# source.  It 307s to a signed S3 URL, which requests follows.
_CSV_URL = "https://doaj.org/csv"

# Where the reduced list is kept between runs.  Beside the app rather than in a
# platform cache directory, so it is obvious what it is and deleting it is
# obviously safe; .gitignore keeps it out of the repository.
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CACHE_FILE = CACHE_DIR / "doaj_journals.json"

# How long a cached list is used before it is fetched again.  A journal
# directory does not turn over quickly, and the cost of being two weeks stale is
# that a very new journal has to be typed by hand — which the box allows anyway.
MAX_CACHE_AGE_SECONDS = 14 * 24 * 3600

DEFAULT_LIMIT = 8

# No key is needed, and DOAJ asks only that clients identify themselves.  Same
# shape as data_quality.py's Crossref agent.
_UA = "bealls-list-check/1.0 (research tooling; +https://doaj.org/docs/api)"

# The CSV's column headings, named once here.  DOAJ has renamed these before, so
# each is looked up with a fallback and a missing one costs that field, not the
# whole load.
_COL_TITLE = "Journal title"
_COL_PISSN = "Journal ISSN (print version)"
_COL_EISSN = "Journal EISSN (online version)"
_COL_PUBLISHER = "Publisher"
_COL_COUNTRY = "Country of publisher"
_COL_URL = "Journal URL"


def _row_to_journal(row):
    """One CSV row as a :class:`Journal`, or ``None`` if it names nothing."""
    issns = []
    for col in (_COL_PISSN, _COL_EISSN):
        issn = normalize_issn(row.get(col))
        if issn and issn not in issns:
            issns.append(issn)
    title = (row.get(_COL_TITLE) or "").strip()
    if not (title or issns):
        return None
    return Venue(
        title=title,
        issns=tuple(issns),
        site=(row.get(_COL_URL) or "").strip(),
        publisher=(row.get(_COL_PUBLISHER) or "").strip(),
        country=(row.get(_COL_COUNTRY) or "").strip(),
        kind="journal",
    )


def _read_cache():
    """The cached list, or ``None`` when it is absent, stale or unreadable."""
    try:
        if not CACHE_FILE.is_file():
            return None
        if time.time() - CACHE_FILE.stat().st_mtime > MAX_CACHE_AGE_SECONDS:
            return None
        with io.open(CACHE_FILE, encoding="utf-8") as fh:
            return [Venue(title=t, issns=tuple(i), site=s, publisher=p,
                          country=c, kind="journal")
                    for t, i, s, p, c in json.load(fh)["journals"]]
    except Exception:
        return None                     # a damaged cache is a missing cache


def _write_cache(journals):
    """Persist the reduced list. Failure is not an error — it just costs a fetch."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"fetched": time.time(),
                   "journals": [[j.title, list(j.issns), j.site, j.publisher, j.country]
                                for j in journals]}
        with io.open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except Exception:
        pass


def load_journals(*, timeout=120.0, refresh=False):
    """Every journal in DOAJ, from the disk cache or one download.

    Returns ``(journals, error)``.  *error* is a sentence for the person at the
    screen and is empty on success; *journals* is empty only when the list could
    not be obtained at all.  Nothing raises — the box this feeds must keep
    working by hand when DOAJ or the network does not.
    """
    if not refresh:
        cached = _read_cache()
        if cached:
            return cached, ""
    try:
        response = requests.get(_CSV_URL, headers={"User-Agent": _UA},
                                timeout=timeout, stream=True)
    except requests.exceptions.RequestException:
        return (_read_cache() or []), ("Could not reach DOAJ to fetch its journal list. "
                                       "Venues can still be added by typing them.")
    if response.status_code != 200:
        return (_read_cache() or []), (
            f"DOAJ answered {response.status_code} for its journal list. "
            "Venues can still be added by typing them.")
    try:
        text = response.content.decode("utf-8", errors="replace")
        rows = csv.DictReader(io.StringIO(text))
        journals = [j for j in (_row_to_journal(r) for r in rows) if j is not None]
    except Exception as exc:
        return (_read_cache() or []), f"DOAJ's journal list could not be read ({exc})."
    if not journals:
        return [], "DOAJ's journal list came back empty."
    _write_cache(journals)
    return journals, ""


def _haystack(journal):
    """The lower-cased text one journal is matched against."""
    return f"{journal.title} {journal.publisher}".lower()


def build_index(journals):
    """Pre-lower every journal's searchable text, once, for :func:`search`.

    Returns a list of ``(haystack, lowered_title, journal)``.  Doing this per
    keystroke over 23,000 journals is the difference between a box that keeps up
    with typing and one that does not.
    """
    return [(_haystack(j), j.title.lower(), j) for j in journals]


def search(index, term, limit=DEFAULT_LIMIT):
    """The best *limit* matches for *term*, and how many there were in all.

    Returns ``(hits, total)``.  *total* is every journal that matched, not just
    the ones returned, so a caller can offer the rest rather than silently
    truncating — "8 of 214" is a different statement from "8".

    *index* is what :func:`build_index` returned (a bare journal list is also
    accepted, and indexed on the spot).  Ranking is by how early and how cleanly
    the term appears, which is what makes a half-typed name useful:

      1. the title starts with it — "power sou" -> *Power Sources…*
      2. a word of the title starts with it — "sources" -> *Journal of Power Sources*
      3. it appears anywhere in the title
      4. it appears in the publisher's name

    An ISSN is matched exactly instead, against either of a journal's two.

    Every match is scanned and ranked, with no cap: a one-letter term does match
    thousands, and stopping early would make *total* a number that depends on
    where the scan happened to stop rather than on how many journals there are.
    Measured at ~40 ms over the whole directory, which a keystroke can afford.
    """
    term = (term or "").strip().lower()
    if not term or not index:
        return [], 0
    if isinstance(index[0], Venue):
        index = build_index(index)

    issn = normalize_issn(term) if ISSN_RE.search(term) else ""
    if issn:
        exact = [j for _h, _t, j in index if issn in j.issns]
        return exact[:limit], len(exact)

    hits = []
    for haystack, title, journal in index:
        if title.startswith(term):
            rank = 0
        elif re.search(r"\b" + re.escape(term), title):
            rank = 1
        elif term in title:
            rank = 2
        elif term in haystack:
            rank = 3
        else:
            continue
        # Shorter titles first within a rank: a search for "nature" should offer
        # *Nature* before *Nature Reviews Methods Primers*.
        hits.append((rank, len(title), journal))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [j for _r, _l, j in hits[:limit]], len(hits)
