"""The DOAJ journal list, held locally so a venue can be found while it is typed.

The Directory of Open Access Journals (https://doaj.org) is the reference index
of vetted open-access journals.  Step 3's **Only check these venues** list is a
*scope filter* — see ``user_lists.py`` — and typing a journal into it by hand
means knowing its exact title, its website, or its ISSN.  DOAJ knows all three,
so this module turns "the journal I mean is called roughly this" into an entry
the check can match on.

**Two ways to search it, and the Settings page picks.**  :func:`search` asks
DOAJ's own API, which is what the whitelist box uses out of the box: no download,
and an answer in about a tenth of a second.  :func:`load_journals` fetches the
entire journal list instead — one CSV, ~23,000 journals, ~25 MB — and reduces it
to the five fields a venue entry needs, after which :func:`search_local` is a
scan of a list in memory: instant from the first character, correct offline, and
no load on DOAJ at all.

*This file used to say the API could not be used at all — that typing four
letters into it earned a Cloudflare 502.  That was too strong, and so was the
first correction to it.  Measured both ways: three bursts, including thirteen
requests with no gap between them at all, answered 200 in 0.05–0.34 s with no
502 anywhere — and then the app, driving it normally an hour later, took a 502
on an ordinary two-word query.  So: the API works, is usually fast, and **does
still fail under load**.  That is why it is the default rather than the only
option, why the failure says so plainly instead of looking like "no match", and
why the message points at the switch that makes it stop happening.*

Nothing in the check engine imports this: DOAJ has no part in any verdict, and
must not acquire one.  Being indexed in DOAJ says a journal passed DOAJ's
editorial screening; it is not the question this tool asks, which is whether the
venue appears on Beall's List.  All this module does is help *name* a venue.

The cache, the index and the ranking are ``venue_lookup``'s, shared with
``crossref.py`` — what is left here is the one thing that is DOAJ's alone: the
address of its CSV and what its columns are called.

Like ``runners.py`` this is import-safe — no Streamlit — so it can be exercised
from a shell:

    >>> journals, err = load_journals()
    >>> search(journals, "power sources adv")[0].issns
    ('2666-2485',)
"""

import csv
import io

import requests

import sys
from pathlib import Path

# Flat imports across ui/'s feature folders — `import styles`, not
# `from ui.styles import styles`.  ui/ is not a package (Streamlit runs app.py as
# a script), so every folder is put on the path first.  Repeated per module
# rather than shared, so each still imports when it is the thing being run: from
# a shell, or by anything that never touched app.py.  Same shim the check
# engine's own foldered modules use — see src/bealls_list_check/*/.
_UI = Path(__file__).resolve().parent.parent
for _p in [_UI, *(d for d in _UI.iterdir() if d.is_dir() and any(d.glob("*.py")))]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from venue_lookup import (CACHE_DIR, ISSN_RE, MAX_CACHE_AGE_SECONDS,  # noqa: F401
                          SearchResult, Venue, build_index, cache_present,
                          cache_status, normalize_issn, read_cache,
                          read_preference, site_is_shared, write_cache,
                          write_preference)
# Aliased, because this module's own `search` is the live API and both are wanted:
# the local copy when there is one, the network when there is not.
from venue_lookup import search as search_local

# The venue shape and the shared-host test live in venue_lookup, so this module
# and crossref.py hand back the same thing. Re-exported under the old name
# because a DOAJ record is always a journal.
Journal = Venue


# DOAJ's own "all journals as CSV" download — one request, refreshed daily at
# source.  It 307s to a signed S3 URL, which requests follows.
_CSV_URL = "https://doaj.org/csv"

# DOAJ's own journal search, one request per term.  Free, no key, and
# comfortably fast enough to sit behind a debounce -- see the module docstring
# for the measurement that retired the claim that it was not.
_API = "https://doaj.org/api/search/journals/"

# Below this many characters nothing is sent: one letter is a real query and
# not a useful answer, and it costs a round trip.  The local copy has no such
# cost and so no such floor.
MIN_TERM = 2

CACHE_FILE = CACHE_DIR / "doaj_journals.json"

# Roughly what the download costs, for the Settings page to say before anyone
# starts it. Measured; DOAJ's list grows slowly, so this stays about right.
DOWNLOAD_MB = 25

DEFAULT_LIMIT = 8

# No key is needed, and DOAJ asks only that clients identify themselves. Same
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


def status():
    """What the Settings page needs to say about the local copy."""
    return cache_status(CACHE_FILE)


def held():
    """Whether the reader has asked for this directory to be held locally."""
    return read_preference("doaj")


def hold(value):
    """Record the answer. Switching *off* keeps the file — see venue_lookup.PREFS_FILE."""
    write_preference("doaj", value)


def downloaded():
    """Whether a local copy is actually on disk, whatever the preference says.

    :func:`cache_present` rather than :func:`status`, because this is asked at
    keystroke rate — see the note there.  ``status()["present"]`` is the same
    answer arrived at by parsing the file to count what is in it.
    """
    return cache_present(CACHE_FILE)


def have_local():
    """Whether searches should go to the local copy: asked for, and actually here.

    Two cheap reads and no parse: this is on the path of every keystroke in the
    whitelist box and every render of Step 3.
    """
    return held() and downloaded()


def delete():
    """Remove the local copy. Returns True if a file was actually removed."""
    try:
        CACHE_FILE.unlink()
        return True
    except Exception:
        return False


def load_journals(*, timeout=120.0, refresh=False, progress=None):
    """Every journal in DOAJ, from the disk cache or one download.

    Returns ``(journals, error)``.  *error* is a sentence for the person at the
    screen and is empty on success; *journals* is empty only when the list could
    not be obtained at all.  Nothing raises — the box this feeds must keep
    working by hand when DOAJ or the network does not.

    *progress* is called with a fraction from 0 to 1 as the download arrives, for
    the Settings page's bar.  It is optional and never required to be accurate:
    DOAJ's CSV is served without a length, in which case it is called with
    ``None`` to mean "running, but the end is not known".
    """
    if not refresh:
        cached = read_cache(CACHE_FILE)
        if cached:
            return cached, ""
    try:
        response = requests.get(_CSV_URL, headers={"User-Agent": _UA},
                                timeout=timeout, stream=True)
    except requests.exceptions.RequestException:
        return (read_cache(CACHE_FILE, max_age=None) or []), (
            "Could not reach DOAJ to fetch its journal list. "
            "Venues can still be added by typing them.")
    if response.status_code != 200:
        return (read_cache(CACHE_FILE, max_age=None) or []), (
            f"DOAJ answered {response.status_code} for its journal list. "
            "Venues can still be added by typing them.")
    try:
        raw = _download(response, progress)
        text = raw.decode("utf-8", errors="replace")
        rows = csv.DictReader(io.StringIO(text))
        journals = [j for j in (_row_to_journal(r) for r in rows) if j is not None]
    except Exception as exc:
        return (read_cache(CACHE_FILE, max_age=None) or []), (
            f"DOAJ's journal list could not be read ({exc}).")
    if not journals:
        return [], "DOAJ's journal list came back empty."
    write_cache(CACHE_FILE, journals)
    return journals, ""


def _download(response, progress=None):
    """Read a streamed response to bytes, reporting progress if asked.

    Shared shape with ``crossref._download``; kept per-module rather than in
    ``venue_lookup`` because that module is deliberately network-free.
    """
    total = 0
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


def _api_journal(record):
    """One API result as a :class:`Journal`, or ``None`` if it names nothing.

    The API nests everything under ``bibjson`` and splits the two ISSNs into
    their own keys, but it carries the same five fields the CSV does — title,
    both ISSNs, publisher, country and the journal's own URL — so a venue found
    online and the same venue found in the local copy are indistinguishable.
    """
    bibjson = (record or {}).get("bibjson") or {}
    issns = []
    for key in ("pissn", "eissn"):
        issn = normalize_issn(bibjson.get(key))
        if issn and issn not in issns:
            issns.append(issn)
    title = (bibjson.get("title") or "").strip()
    if not (title or issns):
        return None
    publisher = bibjson.get("publisher") or {}
    return Venue(
        title=title,
        issns=tuple(issns),
        site=((bibjson.get("ref") or {}).get("journal") or "").strip(),
        publisher=(publisher.get("name") or "").strip(),
        country=(publisher.get("country") or "").strip(),
        kind="journal",
    )


def search(term, *, limit=DEFAULT_LIMIT, timeout=8.0):
    """Search DOAJ's own API for *term*; never raises.

    The counterpart of :func:`search_local`, and the one the whitelist box uses
    when there is no local copy.  Returns a
    :class:`~venue_lookup.SearchResult`; a term shorter than :data:`MIN_TERM` is
    not sent at all and comes back empty with no error.

    An ISSN is asked as an ISSN — ``issn:2666-2485`` — which is the one query
    that can be exact.
    """
    term = (term or "").strip()
    if len(term) < MIN_TERM:
        return SearchResult(term=term)

    issn = normalize_issn(term) if ISSN_RE.search(term) else ""
    query = f"issn:{issn}" if issn else term
    try:
        response = requests.get(_API + requests.utils.quote(query, safe=":"),
                                params={"pageSize": max(1, min(int(limit), 100))},
                                headers={"User-Agent": _UA}, timeout=timeout)
    except requests.exceptions.Timeout:
        return SearchResult(term=term,
                            error=f"DOAJ did not answer within {timeout:g}s. "
                                  "Type the venue and press Add instead.")
    except requests.exceptions.RequestException:
        return SearchResult(term=term,
                            error="Could not reach DOAJ (no internet, or the site is "
                                  "down). Venues can still be added by typing them.")
    if response.status_code == 429:
        return SearchResult(term=term,
                            error="DOAJ is asking for fewer requests just now. Wait a "
                                  "moment, or press Add to use what you typed.")
    if response.status_code != 200:
        # DOAJ does this under load; the local copy is the standing fix, so the
        # message names it rather than only offering the immediate escape.
        return SearchResult(term=term,
                            error=f"DOAJ answered {response.status_code}. Press Add to "
                                  "use what you typed, or set DOAJ to “On this computer” "
                                  "in Settings to stop asking it at all.")
    try:
        payload = response.json()
    except ValueError:
        return SearchResult(term=term, error="DOAJ's answer was not readable as JSON.")

    venues = tuple(v for v in (_api_journal(r) for r in (payload.get("results") or []))
                   if v is not None)
    return SearchResult(venues=venues, total=int(payload.get("total") or 0), term=term)
