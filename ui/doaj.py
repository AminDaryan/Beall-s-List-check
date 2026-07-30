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
after that is a scan of a list in memory: instant from the first character,
correct offline, and no load on DOAJ at all.

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

from venue_lookup import (CACHE_DIR, ISSN_RE, MAX_CACHE_AGE_SECONDS,  # noqa: F401
                          Venue, build_index, cache_status, normalize_issn,
                          read_cache, search, site_is_shared, write_cache)

# The venue shape and the shared-host test live in venue_lookup, so this module
# and crossref.py hand back the same thing. Re-exported under the old name
# because a DOAJ record is always a journal.
Journal = Venue


# DOAJ's own "all journals as CSV" download — one request, refreshed daily at
# source.  It 307s to a signed S3 URL, which requests follows.
_CSV_URL = "https://doaj.org/csv"

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


def have_local():
    """Whether a local copy exists. Read from the file's own stat, so it is cheap
    enough to ask on every render."""
    return bool(cache_status(CACHE_FILE)["present"])


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
