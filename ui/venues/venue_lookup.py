"""What the two venue directories have in common: a venue, and a host's breadth.

Step 3's boxes look venues up in two different places, for two different reasons:

  * the **whitelist** searches the DOAJ (``doaj.py``), which lists vetted
    open-access journals — the venues someone narrowing a run to "the journals I
    care about" is naming;
  * the **blacklist** searches Crossref (``crossref.py``), the DOI registry,
    which lists whichever journals their publisher pays to register and screens
    none of them — which is the only kind of directory that can help name a venue
    someone believes is predatory, since such a venue is by definition not in a
    directory of screened ones.

Both hand back the same :class:`Venue`, and both have to answer the same
question about a website before offering it, so those two things live here
rather than in whichever module happened to need them first.  Only DOAJ's
records actually carry one — a Crossref journal record has no homepage field —
so a venue from the blacklist's directory arrives with an empty :attr:`Venue.site`
and :func:`site_is_shared` simply answers False for it.

**And both can be held locally**, which is the other half of this module: each
directory publishes its whole journal list as one file, and once that file has
been reduced and cached, searching it is a scan of a list in memory rather than a
request to anyone.  DOAJ has always been used that way.  Crossref need not be —
it has a live search API — but it can be, and on a machine with no internet it is
the only way it works at all.  The parts that do not depend on *which* directory
it is — the cache on disk, the pre-lowered index, the ranking — are here, so the
two modules are left holding only what is actually different about them: where
the file is and how to read a row of it.

Import-safe: no Streamlit, no network.
"""

import io
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# The engine's normalizers, so a domain written into a venue list here is keyed
# exactly as the matcher will key it later.  Imported defensively (the same shim
# runners.py uses for STATUS_FILL_COLORS) so this module still imports on its own
# if the package layout moves under it.
_BEALLS = Path(__file__).resolve().parent.parent / "src" / "bealls_list_check"
try:
    if str(_BEALLS) not in sys.path:
        sys.path.insert(0, str(_BEALLS))
    from normalize import normalize_host, normalize_issn
except Exception:                                   # pragma: no cover - fallback
    def normalize_host(url_or_host):
        if not url_or_host:
            return ""
        raw = str(url_or_host).strip()
        if "//" not in raw:
            raw = "//" + raw
        host = urlparse(raw).netloc.lower().split("@")[-1].split(":")[0]
        return host[4:] if host.startswith("www.") else host

    def normalize_issn(issn):
        m = re.search(r"\b(\d{4})-?(\d{3}[\dxX])\b", str(issn or ""))
        return f"{m.group(1)}-{m.group(2).upper()}" if m else ""


ISSN_RE = re.compile(r"\b\d{4}-?\d{3}[\dxX]\b")

# Hosts known to carry many venues.  The path test in :func:`site_is_shared`
# catches most of these on its own (the venue is named by the path, so the host
# is broader than the venue), but a publisher whose journal sits at the root of a
# subdomain — jenci.springeropen.com — needs telling.  Deliberately short: this
# only decides how emphatic a caution is, never what the run does.
_PLATFORM_HOSTS = (
    "sciencedirect.com", "elsevier.com", "springer.com", "springeropen.com",
    "biomedcentral.com", "nature.com", "wiley.com", "tandfonline.com",
    "sagepub.com", "oup.com", "cambridge.org", "ieee.org", "acm.org",
    "frontiersin.org", "mdpi.com", "hindawi.com", "karger.com",
    "degruyter.com", "emerald.com", "inderscience.com", "lww.com",
    "keaipublishing.com", "bmj.com", "plos.org", "wolterskluwer.com",
    "dovepress.com", "dergipark.org.tr", "dergipark.gov.tr",
    "jstage.jst.go.jp", "scielo.br", "redalyc.org", "sanad.iau.ir",
    "digitalcommons.", "books.google.com", "ieeexplore.ieee.org",
)


def site_is_shared(url):
    """True if *url*'s host looks like it carries venues besides this one.

    Two signals, both structural:

      * the host is a publisher or national platform we know by name;
      * the venue is identified by the URL's **path** rather than by its host
        (``www.nature.com/commseng/``), which means the host is the platform and
        the path is the venue.

    A bare host (``https://www.ejcrim.com/``) and a single-journal OJS install
    (``https://mlj.goums.ac.ir/index.php``) are the venue's own site and score
    False.  Sampled against 300 live DOAJ records this errs toward True, which is
    the harmless direction: no domain is added unless asked for, so the worst a
    false positive does is caution about a site that did not need it.
    """
    host = normalize_host(url)
    if not host:
        return False
    if any(p in host for p in _PLATFORM_HOSTS):
        return True
    segments = [s for s in (urlparse(str(url)).path or "").split("/") if s]
    return bool(segments) and segments != ["index.php"]


@dataclass(frozen=True)
class Venue:
    """One journal or publisher, reduced to what naming a venue needs.

    The same shape whichever directory it came from, so the box that offers it
    and the entry it becomes do not have to know which one that was.
    """

    title: str
    issns: tuple = ()                 # normalized, deduped, most canonical first
    site: str = ""                    # the venue's own homepage
    publisher: str = ""
    country: str = ""
    kind: str = ""                    # "journal", "repository", … where known

    @property
    def host(self):
        """The registrable host of :attr:`site` — what a domain entry keys on."""
        return normalize_host(self.site)

    @property
    def site_is_shared(self):
        return site_is_shared(self.site)

    def to_entry(self, *, include_site=False):
        """This venue as a list entry, in ``user_lists.py``'s JSON format.

        Title and ISSNs always; the website only when asked for.  It is left out
        by default because the entry is meant to name *this venue*, and the
        matcher keys a domain on its host alone — so adding the site of a journal
        published on a platform silently widens the entry to every other journal
        on that platform.  See :func:`site_is_shared`.

        The site is written as ``domain`` (the bare host) rather than as ``url``,
        even though both keys are accepted, so that the JSON says what the run
        will actually match on instead of implying the path matters.
        """
        entry = {"name": self.title}
        if self.issns:
            entry["issns"] = list(self.issns)
        if include_site and self.host:
            entry["domain"] = self.host
        return entry


@dataclass(frozen=True)
class SearchResult:
    """What one *live* search produced — including, on failure, why it produced nothing.

    Both directories can be searched over the network as well as locally, and both
    have to be able to fail without raising: the result is cached by the caller,
    and an exception is not cacheable, so a refused query would otherwise be
    re-sent on every rerun of the page.
    """

    venues: tuple = ()
    total: int = 0                 # matches the directory holds, which exceeds len(venues)
    term: str = ""
    error: str = ""                # a sentence for the user; "" when the search ran

    @property
    def ok(self):
        return not self.error


def option_label(venue):
    """One line for the search menu: the venue, and what adding it would key on."""
    bits = [venue.title or ", ".join(venue.issns) or "untitled"]
    if venue.issns:
        bits.append(" · ".join(venue.issns))
    if venue.publisher:
        bits.append(venue.publisher)
    if venue.host:
        bits.append(f"⚠ {venue.host}" if venue.site_is_shared else venue.host)
    return "  —  ".join(bits)


# ── The local copy: where it lives, and how it is read ──────────────────────
#
# Beside the app rather than in a platform cache directory, so it is obvious what
# it is and deleting it is obviously safe; .gitignore keeps it out of the
# repository.  The Settings page reports and manages both files.
CACHE_DIR = Path(__file__).resolve().parent / ".cache"

# How old a cached list has to be before it is worth fetching again.  A journal
# directory does not turn over quickly, and the cost of being two weeks stale is
# that a very new journal has to be typed by hand — which the box allows anyway.
#
# Which is why this is the *Settings page's* threshold and not the venue boxes'.
# A search reads whatever copy is on disk however old it is (see
# ``venue_lists.doaj_index``): the alternative there is not a fresher list, it is
# no list at all, or a 51 MB download from inside a keystroke.
MAX_CACHE_AGE_SECONDS = 14 * 24 * 3600


# Which directories the reader has asked to hold locally.  A file rather than
# session state, because the answer has to survive closing the app; beside the
# caches it describes, and the same to delete.
#
# Kept *separate from whether the file is there*, which is the distinction the
# switch turns on: off means "search this online", not "throw away the 18 MB you
# downloaded".  Turning it back on with the copy still present costs nothing.
PREFS_FILE = CACHE_DIR / "directories.json"


def read_preference(name, default=False):
    """Whether *name* is set to be held locally.  Unreadable prefs read as default."""
    try:
        with io.open(PREFS_FILE, encoding="utf-8") as fh:
            return bool(json.load(fh).get(name, default))
    except Exception:
        return default


def write_preference(name, value):
    """Record whether *name* is held locally.  Failure costs the preference, not the app."""
    try:
        prefs = {}
        if PREFS_FILE.is_file():
            with io.open(PREFS_FILE, encoding="utf-8") as fh:
                prefs = json.load(fh)
        prefs[name] = bool(value)
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with io.open(PREFS_FILE, "w", encoding="utf-8") as fh:
            json.dump(prefs, fh)
    except Exception:
        pass


def read_cache(path, max_age=MAX_CACHE_AGE_SECONDS):
    """The cached list at *path*, or ``None`` if absent, stale or unreadable.

    A damaged cache is treated as a missing one: there is a way to rebuild it, and
    guessing at half a file is worse than fetching again.  *max_age* of ``None``
    reads it however old it is, which is what a caller wants when age is not its
    question — the Settings page reporting on a file rather than deciding whether
    to trust it, and the venue boxes, which have no fresher list to fall back to.
    """
    try:
        path = Path(path)
        if not path.is_file():
            return None
        if max_age is not None and time.time() - path.stat().st_mtime > max_age:
            return None
        with io.open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        return [Venue(title=t, issns=tuple(i), site=s, publisher=p, country=c,
                      kind="journal")
                for t, i, s, p, c in payload["journals"]]
    except Exception:
        return None


def write_cache(path, journals):
    """Persist the reduced list. Failure is not an error — it only costs a fetch.

    Five positional fields rather than a dict per journal, because there are tens
    of thousands of them and the key names would be most of the file.
    """
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched": time.time(),
                   "journals": [[j.title, list(j.issns), j.site, j.publisher, j.country]
                                for j in journals]}
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except Exception:
        pass


def cache_present(path):
    """Whether there is a local copy at *path*.  A stat, and nothing else.

    The question the venue boxes ask, and the reason it is not :func:`cache_status`
    with the rest of the answer thrown away.  That function reports the journal
    count, which costs a parse of the whole file — so a test that only needed
    ``is_file`` was running ``json.load`` over 17.6 MB of Crossref on every
    keystroke and on every render of Step 3.  Measured: 405 ms to answer *is the
    file there*, against 13 ms to actually search the 167,000 journals in it.

    Deliberately **not** an opinion about whether that copy is any good.  A present
    but damaged file answers True here and is caught where it is read, by
    :func:`read_cache` returning ``None``.  The Settings page does need the
    stronger question — it decides whether to download — and asks
    :func:`cache_status` for it, once per render of a page nobody spends the day
    on.
    """
    try:
        return Path(path).is_file()
    except Exception:
        return False


def cache_status(path):
    """What the Settings page needs to say about one local copy.

    ``journals`` is ``-1`` when the file is there but its count could not be read,
    which is the honest answer for a file that exists and is damaged — and is what
    the Settings page tests, because a truncated copy is one to replace rather
    than one to congratulate itself on already having.

    **This is not cheap, and the count is why.**  Counting the records means
    parsing the file: 405 ms over Crossref's 17.6 MB, measured.  It is affordable
    here because the Settings page renders when someone opens it, and nowhere
    else.  Anything asked more often than that wants :func:`cache_present`, which
    is a stat — this docstring used to claim the count came for free, and a
    keystroke-rate caller believed it.
    """
    path = Path(path)
    if not path.is_file():
        return {"present": False, "journals": 0, "bytes": 0, "fetched": 0.0,
                "age_days": 0.0, "stale": False, "path": path}
    stat = path.stat()
    journals = -1
    try:
        with io.open(path, encoding="utf-8") as fh:
            journals = len(json.load(fh)["journals"])
    except Exception:
        pass
    age = max(time.time() - stat.st_mtime, 0.0)
    return {"present": True, "journals": journals, "bytes": stat.st_size,
            "fetched": stat.st_mtime, "age_days": age / 86400,
            "stale": age > MAX_CACHE_AGE_SECONDS, "path": path}


def _haystack(venue):
    """The lower-cased text one venue is matched against."""
    return f"{venue.title} {venue.publisher}".lower()


def build_index(journals):
    """Pre-lower every venue's searchable text, once, for :func:`search`.

    Returns a list of ``(haystack, lowered_title, venue)``.  Doing this per
    keystroke over tens of thousands of journals is the difference between a box
    that keeps up with typing and one that does not.
    """
    return [(_haystack(j), j.title.lower(), j) for j in journals]


def search(index, term, limit=8):
    """The best *limit* matches for *term*, and how many there were in all.

    Returns ``(hits, total)``.  *total* is every venue that matched, not just the
    ones returned, so a caller can offer the rest rather than silently truncating
    — "8 of 214" is a different statement from "8".

    *index* is what :func:`build_index` returned (a bare venue list is also
    accepted, and indexed on the spot).  Ranking is by how early and how cleanly
    the term appears, which is what makes a half-typed name useful:

      1. the title starts with it — "power sou" -> *Power Sources…*
      2. a word of the title starts with it — "sources" -> *Journal of Power Sources*
      3. it appears anywhere in the title
      4. it appears in the publisher's name

    An ISSN is matched exactly instead, against any of a venue's ISSNs.

    Every match is scanned and ranked, with no cap: a one-letter term does match
    thousands, and stopping early would make *total* a number that depends on
    where the scan happened to stop rather than on how many journals there are.
    Measured at ~40 ms over DOAJ's 23,000 and ~250 ms over Crossref's 169,000,
    which a keystroke can afford.
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

    # One substring test rejects almost everything, and it is the cheapest test
    # available — `in` on a str is C, the word-boundary search is not. Every rank
    # below implies the term is somewhere in the haystack (the title is part of
    # it), so this filters without changing a single verdict. Measured over
    # Crossref's 167,000: 250ms before, 45ms after, because the regex now runs
    # only on the few hundred rows that already matched.
    hits = []
    word = re.compile(r"\b" + re.escape(term))
    for haystack, title, venue in index:
        if term not in haystack:
            continue
        if title.startswith(term):
            rank = 0
        elif word.search(title):
            rank = 1
        elif term in title:
            rank = 2
        else:
            rank = 3
        # Shorter titles first within a rank: a search for "nature" should offer
        # *Nature* before *Nature Reviews Methods Primers*.
        hits.append((rank, len(title), venue))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [j for _r, _l, j in hits[:limit]], len(hits)
