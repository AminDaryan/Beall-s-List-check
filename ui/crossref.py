"""Look venues up in Crossref, for the blacklist.

The blacklist names venues the user asserts are predatory.  DOAJ cannot help
there by construction — it lists journals that *passed* an editorial screening,
so the venue someone wants to blacklist is precisely the one it does not have.
`Crossref <https://www.crossref.org>`_ is the DOI registry: a journal is in it
because its publisher pays to register DOIs, which screens for nothing except
membership.  That is what makes it the right directory here — registering DOIs
is part of *looking* established, so the venues a blacklist is for are in it, and
each one comes with the title, ISSNs and publisher name an entry needs.

The tool already talks to Crossref: ``data_quality.py`` resolves a paper's DOI
there to name the journal it was really published in.  This module is a second,
unrelated use of the same service and the same ``CROSSREF_MAILTO`` — no new
dependency, and one address to set rather than two.

**On calling it while a name is typed.**  Crossref meters by rate rather than by
budget, and the numbers are in every response: ``X-Rate-Limit-Limit: 5`` per
``X-Rate-Limit-Interval: 1s`` with ``X-Concurrency-Limit: 1`` anonymously,
doubled to 10/s and 3 concurrent for callers who identify themselves.  A search
takes ~0.55 s, so with the debounce in ``app.py`` typing comes nowhere near
either ceiling — but a request is still a request, so this module is called only
from two characters up and through a cache.  :func:`search` returns its failures
instead of raising, so the caller can cache them too — an exception is not
cacheable, and a refused query would otherwise be re-sent on every rerun of the
page.

**What a Crossref journal record does *not* carry** is a website: the fields are
the title, the ISSNs, the publisher and usage statistics, and nothing that names
a homepage.  So a venue added from here is a name and its ISSNs, never a domain.
That is the safe direction anyway — a domain is matched by host, so it would
cover every other journal on the same platform — and it is why
:class:`~venue_lookup.Venue` comes back from here with an empty ``site``.

Nothing in the check engine imports this: Crossref has no part in any verdict of
this kind, and must not acquire one.  Being registered there says nothing about a
venue at all — it is a registry, not a judgement.  All this module does is help
*name* one.

Import-safe (no Streamlit), so it can be exercised from a shell:

    >>> result = search("wseas")
    >>> result.venues[0].title
    'WSEAS TRANSACTIONS ON MATHEMATICS'
"""

import csv
import io
import os
from dataclasses import dataclass

import requests

from venue_lookup import (CACHE_DIR, ISSN_RE, MAX_CACHE_AGE_SECONDS,  # noqa: F401
                          Venue, build_index, cache_status, normalize_issn,
                          read_cache, write_cache)
# Aliased, because this module's own `search` is the live API and both are wanted:
# the local copy when there is one, the network when there is not.
from venue_lookup import search as search_local

_API = "https://api.crossref.org/journals"

# Crossref's own "every journal we know of" file: title, publisher and ISSNs for
# all 168,706 of them (measured) in one 50 MB CSV.  This is what makes an offline
# copy practical -- the same list by the API is 169 pages of ~3.7 MB, about 630 MB
# and five and a half minutes, because `/journals` refuses `select` (a 400, also
# measured) and so hands back every field it has, statistics included.
_TITLES_URL = "https://ftp.crossref.org/titlelist/titleFile.csv"

CACHE_FILE = CACHE_DIR / "crossref_journals.json"

# Roughly what the download costs, for the Settings page to say before anyone
# starts it. Measured at 50.5 MB; Crossref's list grows, so this is a floor.
DOWNLOAD_MB = 51

# The title file's columns.  Its ISSNs arrive without the hyphen (`31077102`),
# which `normalize_issn` puts back, and its titles carry leading non-breaking
# spaces and direction marks often enough to be worth stripping by hand.
_COL_TITLE = "JournalTitle"
_COL_PUBLISHER = "Publisher"
_COL_PISSN = "pissn"
_COL_EISSN = "eissn"
_COL_MORE_ISSNS = "additionalIssns"

# Crossref asks callers to identify themselves and gives the ones that do a
# faster, more reliable "polite pool" — measured: the rate limit reported in the
# response headers goes from 5/s and 1 concurrent request to 10/s and 3.  Set
# CROSSREF_MAILTO in .env to opt in; harmless (the anonymous pool) if unset.  The
# same variable data_quality.py's DOI lookups read, deliberately.
_MAILTO = os.environ.get("CROSSREF_MAILTO", "").strip()
_UA = ("bealls-list-check/1.0 (research tooling"
       + (f"; mailto:{_MAILTO}" if _MAILTO else "") + ")")

# Below this many characters nothing is sent.  A single letter is a real query to
# Crossref — "w" answers with 342 journals whose title has a word starting that
# way — but it is not an answer anyone can use, and it costs a round trip and
# ~25 KB.  Two letters already mean something ("ie" offers IEEE's).  The local
# DOAJ index that the whitelist searches has no such cost and therefore no floor.
MIN_TERM = 2
DEFAULT_LIMIT = 8

# The most rows one request will ask for, however far Show more has widened the
# window.  Crossref allows far more; this is about the response, which is ~3 KB
# per journal because the endpoint has no field selector (``select`` answers 400
# here, unlike on ``/works``).  Ten Show more presses to reach it.
_MAX_ROWS = 200


@dataclass(frozen=True)
class SearchResult:
    """What one search produced — including, on failure, why it produced nothing."""

    venues: tuple = ()
    total: int = 0                 # matches Crossref holds, which exceeds len(venues)
    term: str = ""
    error: str = ""                # a sentence for the user; "" when the search ran

    @property
    def ok(self):
        return not self.error


def _issns(record):
    """A journal's ISSNs, normalized and deduped, print first then electronic.

    Crossref gives them twice: ``ISSN`` as a bare list in no stated order, and
    ``issn-type`` with each one labelled.  The labelled form is preferred so the
    order matches what ``doaj.py`` produces from its two columns, and the bare
    list is the fallback for a record that has only that.
    """
    order = {"print": 0, "electronic": 1}
    typed = [t for t in (record.get("issn-type") or []) if isinstance(t, dict)]
    typed.sort(key=lambda t: order.get((t.get("type") or "").lower(), 2))
    raw = [t.get("value") for t in typed] or list(record.get("ISSN") or [])
    issns = []
    for issn in raw:
        normalized = normalize_issn(issn)
        if normalized and normalized not in issns:
            issns.append(normalized)
    return tuple(issns)


def _venue(record):
    """One Crossref journal as a :class:`Venue`, or ``None`` if it names nothing."""
    issns = _issns(record)
    title = (record.get("title") or "").strip()
    if not (title or issns):
        return None
    # No site and no country: Crossref's journal records carry neither.  See the
    # module docstring — the entry this becomes is a name and its ISSNs.
    return Venue(
        title=title,
        issns=issns,
        publisher=(record.get("publisher") or "").strip(),
        kind="journal",
    )


def search(term, *, limit=DEFAULT_LIMIT, timeout=8.0):
    """Search Crossref's journals for *term*; never raises.

    Returns a :class:`SearchResult`.  A term shorter than :data:`MIN_TERM` is not
    sent at all and comes back empty with no error — it is not a failure, just
    not yet a question worth a round trip.

    An ISSN is looked up as an ISSN, against ``/journals/<issn>``, which is the
    one query that can be exact.  A 404 from that path is an answer rather than a
    fault: no journal is registered under that ISSN.
    """
    term = (term or "").strip()
    if len(term) < MIN_TERM:
        return SearchResult(term=term)

    issn = normalize_issn(term) if ISSN_RE.search(term) else ""
    params = {"mailto": _MAILTO} if _MAILTO else {}
    if issn:
        url = f"{_API}/{issn}"
    else:
        url = _API
        params["query"] = term
        params["rows"] = max(1, min(int(limit), _MAX_ROWS))

    try:
        response = requests.get(url, params=params, headers={"User-Agent": _UA},
                                timeout=timeout)
    except requests.exceptions.Timeout:
        return SearchResult(term=term,
                            error=f"Crossref did not answer within {timeout:g}s. "
                                  "Type the venue and press Add instead.")
    except requests.exceptions.RequestException:
        return SearchResult(term=term,
                            error="Could not reach Crossref (no internet, or the site is "
                                  "down). Venues can still be added by typing them.")

    if issn and response.status_code == 404:
        return SearchResult(term=term)     # no journal has that ISSN — a result, not an error
    if response.status_code == 429:
        return SearchResult(term=term,
                            error="Crossref is asking for fewer requests just now. Wait a "
                                  "moment, or press Add to use what you typed.")
    if response.status_code != 200:
        return SearchResult(term=term,
                            error=f"Crossref answered {response.status_code}. "
                                  "Type the venue and press Add instead.")
    try:
        payload = response.json()
    except ValueError:
        return SearchResult(term=term, error="Crossref's answer was not readable as JSON.")

    message = payload.get("message") or {}
    # The two paths answer in different shapes: a search wraps its hits in
    # `items` and counts them in `total-results`, while an ISSN lookup *is* the
    # one journal.
    records = [message] if issn else (message.get("items") or [])
    venues = tuple(v for v in (_venue(r) for r in records) if v is not None)
    total = len(venues) if issn else int(message.get("total-results") or 0)
    return SearchResult(venues=venues, total=total, term=term)


# ── The local copy ──────────────────────────────────────────────────────────
#
# Everything above talks to the live API, which is what the box uses out of the
# box: it needs no download, and answers in about half a second.  Everything below
# is the alternative offered on the Settings page — Crossref's whole journal list,
# fetched once and searched locally after that.  Faster (no round trip at all),
# works with no internet, and puts nothing on Crossref; the trade is a 51 MB
# download and a list that is as old as the day it was fetched.
#
# The cache, the index and the ranking are `venue_lookup`'s, shared with
# `doaj.py`.  What is Crossref's alone is the address of the file and its columns.


def _clean_title(raw):
    """A title from the file, with the junk its first characters often carry.

    Measured across the list: leading non-breaking spaces (``\xa0``) and
    left-to-right marks (``\u200e``) are common enough that stripping only ASCII
    whitespace leaves titles that sort and match wrongly.
    """
    return (raw or "").strip().strip("\u00a0\u200e\u200f\ufeff").strip()


def _row_to_journal(row):
    """One row of the title file as a :class:`Venue`, or ``None`` if it names nothing."""
    issns = []
    for col in (_COL_PISSN, _COL_EISSN, _COL_MORE_ISSNS):
        # additionalIssns holds several, comma-separated; the other two hold one.
        for part in (row.get(col) or "").replace(";", ",").split(","):
            normalized = normalize_issn(part)
            if normalized and normalized not in issns:
                issns.append(normalized)
    title = _clean_title(row.get(_COL_TITLE))
    if not (title or issns):
        return None
    # No site and no country: the title file carries neither, exactly as the API
    # does not. See the module docstring.
    return Venue(title=title, issns=tuple(issns),
                 publisher=_clean_title(row.get(_COL_PUBLISHER)), kind="journal")


def status():
    """What the Settings page needs to say about the local copy."""
    return cache_status(CACHE_FILE)


def delete():
    """Remove the local copy. Returns True if a file was actually removed."""
    try:
        CACHE_FILE.unlink()
        return True
    except Exception:
        return False


def have_local():
    """Whether a local copy exists and is fresh enough to search.

    Read from the file's own timestamp rather than by loading it, so the question
    is cheap enough to ask on every render.
    """
    return bool(cache_status(CACHE_FILE)["present"])


def load_journals(*, timeout=300.0, refresh=False, progress=None):
    """Every journal Crossref lists, from the disk cache or one download.

    Returns ``(journals, error)`` on the same terms as ``doaj.load_journals``:
    *error* is a sentence for the person at the screen, and nothing raises.  The
    timeout is generous because this is 51 MB rather than DOAJ's 25.

    *progress* is called with a fraction from 0 to 1 as the download arrives, or
    with ``None`` when the server sends no length.
    """
    if not refresh:
        cached = read_cache(CACHE_FILE)
        if cached:
            return cached, ""
    try:
        response = requests.get(_TITLES_URL, headers={"User-Agent": _UA},
                                timeout=timeout, stream=True)
    except requests.exceptions.RequestException:
        return (read_cache(CACHE_FILE, max_age=None) or []), (
            "Could not reach Crossref to fetch its journal list. The box falls "
            "back to searching Crossref live, which also needs a connection.")
    if response.status_code != 200:
        return (read_cache(CACHE_FILE, max_age=None) or []), (
            f"Crossref answered {response.status_code} for its journal list.")
    try:
        raw = _download(response, progress)
        text = raw.decode("utf-8", errors="replace")
        rows = csv.DictReader(io.StringIO(text))
        journals = [j for j in (_row_to_journal(r) for r in rows) if j is not None]
    except Exception as exc:
        return (read_cache(CACHE_FILE, max_age=None) or []), (
            f"Crossref's journal list could not be read ({exc}).")
    if not journals:
        return [], "Crossref's journal list came back empty."
    write_cache(CACHE_FILE, journals)
    return journals, ""


def _download(response, progress=None):
    """Read a streamed response to bytes, reporting progress if asked."""
    try:
        total = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        total = 0
    chunks, seen = [], 0
    for chunk in response.iter_content(262144):
        if not chunk:
            continue
        chunks.append(chunk)
        seen += len(chunk)
        if progress is not None:
            progress(min(seen / total, 1.0) if total else None)
    return b"".join(chunks)
