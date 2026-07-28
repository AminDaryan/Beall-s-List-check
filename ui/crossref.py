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

import os
from dataclasses import dataclass

import requests

from venue_lookup import ISSN_RE, Venue, normalize_issn

_API = "https://api.crossref.org/journals"

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
