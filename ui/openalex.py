"""Look venues up in OpenAlex, for the blacklist.

The blacklist names venues the user asserts are predatory.  DOAJ cannot help
there by construction — it lists journals that *passed* an editorial screening,
so the venue someone wants to blacklist is precisely the one it does not have.
`OpenAlex <https://openalex.org>`_ indexes every source it can find, screened or
not, which makes it the directory that can put a name, an ISSN and a website to a
venue nobody vouches for.

**On calling it while a name is typed.**  OpenAlex meters requests, and the
numbers are in every response: ``X-RateLimit-Limit: 1000`` with
``X-RateLimit-Credits-Used: 10`` per search — about a hundred searches per
``X-RateLimit-Reset`` window (~7 hours) without an account.  That is plenty for
adding venues to a list and nowhere near enough for a request per keystroke, so
this module is called only from three characters up, behind a debounce, and
through a cache; and when the budget does run out it says so rather than going
quiet.  :func:`search` returns its failures instead of raising, so the caller can
cache them too — an exception is not cacheable, and a refused query would
otherwise be re-sent on every rerun of the page.

Nothing in the check engine imports this: OpenAlex has no part in any verdict,
and must not acquire one.  Being indexed there says nothing about a venue at all
— it is an index, not a judgement.  All this module does is help *name* one.

Import-safe (no Streamlit), so it can be exercised from a shell:

    >>> result = search("omics")
    >>> result.venues[0].title
    'OMICS A Journal of Integrative Biology'
"""

import os
from dataclasses import dataclass

import requests

from venue_lookup import ISSN_RE, Venue, normalize_issn

_API = "https://api.openalex.org/sources"

# OpenAlex asks callers to identify themselves and gives the ones that do a
# faster, more reliable pool.  Set OPENALEX_MAILTO in .env to opt in; harmless
# (the anonymous pool) if unset.  Same arrangement as data_quality.py's Crossref
# client, which reads CROSSREF_MAILTO.
_MAILTO = os.environ.get("OPENALEX_MAILTO", "").strip()
_UA = ("bealls-list-check/1.0 (research tooling"
       + (f"; mailto:{_MAILTO}" if _MAILTO else "") + ")")

# Below this many characters nothing is sent.  One or two letters match tens of
# thousands of sources, so the answer is useless *and* it spends a request from a
# budget of about a hundred; the local DOAJ index that the whitelist searches has
# no such cost and therefore no such floor.
MIN_TERM = 3
DEFAULT_LIMIT = 8

# The fields worth asking for, so the response is a fraction of the full record.
_FIELDS = "display_name,issn_l,issn,homepage_url,host_organization_name,country_code,type"


@dataclass(frozen=True)
class SearchResult:
    """What one search produced — including, on failure, why it produced nothing."""

    venues: tuple = ()
    total: int = 0                 # matches OpenAlex holds, which exceeds len(venues)
    term: str = ""
    error: str = ""                # a sentence for the user; "" when the search ran
    budget_left: int = -1          # requests left in the window, -1 if not reported

    @property
    def ok(self):
        return not self.error


def _venue(record):
    """One OpenAlex source as a :class:`Venue`, or ``None`` if it names nothing."""
    issns = []
    for issn in [record.get("issn_l")] + list(record.get("issn") or []):
        normalized = normalize_issn(issn)
        if normalized and normalized not in issns:
            issns.append(normalized)
    title = (record.get("display_name") or "").strip()
    if not (title or issns):
        return None
    return Venue(
        title=title,
        issns=tuple(issns),
        site=(record.get("homepage_url") or "").strip(),
        publisher=(record.get("host_organization_name") or "").strip(),
        country=(record.get("country_code") or "").strip(),
        kind=(record.get("type") or "").strip(),
    )


def _budget(response):
    """Requests left in this rate-limit window, or -1 when it is not reported."""
    try:
        remaining = int(response.headers.get("X-RateLimit-Remaining", ""))
        cost = int(response.headers.get("X-RateLimit-Credits-Used", "") or 10)
        return remaining // max(cost, 1)
    except (TypeError, ValueError):
        return -1


def search(term, *, limit=DEFAULT_LIMIT, timeout=8.0):
    """Search OpenAlex's sources for *term*; never raises.

    Returns a :class:`SearchResult`.  A term shorter than :data:`MIN_TERM` is not
    sent at all and comes back empty with no error — it is not a failure, just
    not yet a question worth spending a request on.

    An ISSN is looked up as an ISSN rather than as text, which is the one query
    that can be exact.
    """
    term = (term or "").strip()
    if len(term) < MIN_TERM:
        return SearchResult(term=term)

    params = {"per-page": max(1, min(int(limit), 50)), "select": _FIELDS}
    issn = normalize_issn(term) if ISSN_RE.search(term) else ""
    if issn:
        params["filter"] = f"issn:{issn}"
    else:
        params["search"] = term
    if _MAILTO:
        params["mailto"] = _MAILTO

    try:
        response = requests.get(_API, params=params, headers={"User-Agent": _UA},
                                timeout=timeout)
    except requests.exceptions.Timeout:
        return SearchResult(term=term,
                            error=f"OpenAlex did not answer within {timeout:g}s. "
                                  "Type the venue and press Add instead.")
    except requests.exceptions.RequestException:
        return SearchResult(term=term,
                            error="Could not reach OpenAlex (no internet, or the site is "
                                  "down). Venues can still be added by typing them.")

    if response.status_code == 429:
        return SearchResult(term=term, budget_left=0,
                            error="OpenAlex's search quota for this address is used up for "
                                  "now. Venues can still be added by typing them.")
    if response.status_code != 200:
        return SearchResult(term=term, budget_left=_budget(response),
                            error=f"OpenAlex answered {response.status_code}. "
                                  "Type the venue and press Add instead.")
    try:
        payload = response.json()
    except ValueError:
        return SearchResult(term=term, error="OpenAlex's answer was not readable as JSON.")

    venues = tuple(v for v in (_venue(r) for r in (payload.get("results") or []))
                   if v is not None)
    total = int(((payload.get("meta") or {}).get("count")) or 0)
    return SearchResult(venues=venues, total=total, term=term,
                        budget_left=_budget(response))
