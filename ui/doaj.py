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
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

# The engine's normalizers, so a domain written into the whitelist here is keyed
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

_ISSN_RE = re.compile(r"\b\d{4}-?\d{3}[\dxX]\b")

# The CSV's column headings, named once here.  DOAJ has renamed these before, so
# each is looked up with a fallback and a missing one costs that field, not the
# whole load.
_COL_TITLE = "Journal title"
_COL_ALT = "Alternative title"
_COL_PISSN = "Journal ISSN (print version)"
_COL_EISSN = "Journal EISSN (online version)"
_COL_PUBLISHER = "Publisher"
_COL_COUNTRY = "Country of publisher"
_COL_URL = "Journal URL"


# Hosts known to carry many journals.  The path test in :func:`site_is_shared`
# catches most of these on its own (the journal is named by the path, so the host
# is broader than the journal), but a publisher whose journal sits at the root of
# a subdomain — jenci.springeropen.com — needs telling.  Deliberately short: this
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
    "digitalcommons.",
)


def site_is_shared(url):
    """True if *url*'s host looks like it carries journals besides this one.

    Two signals, both structural:

      * the host is a publisher or national platform we know by name;
      * the journal is identified by the URL's **path** rather than by its host
        (``www.nature.com/commseng/``), which means the host is the platform and
        the path is the journal.

    A bare host (``https://www.ejcrim.com/``) and a single-journal OJS install
    (``https://mlj.goums.ac.ir/index.php``) are the journal's own site and score
    False.  Sampled against 300 live DOAJ records this errs toward True, which is
    the harmless direction: the caller adds no domain unless asked, so the worst
    a false positive does is caution about a site that did not need it.
    """
    host = normalize_host(url)
    if not host:
        return False
    if any(p in host for p in _PLATFORM_HOSTS):
        return True
    segments = [s for s in (urlparse(str(url)).path or "").split("/") if s]
    return bool(segments) and segments != ["index.php"]


@dataclass(frozen=True)
class Journal:
    """One DOAJ journal, reduced to what naming a venue needs."""

    title: str
    issns: tuple = ()                 # print then electronic, normalized, deduped
    site: str = ""                    # the journal's own homepage
    publisher: str = ""
    country: str = ""

    @property
    def host(self):
        """The registrable host of :attr:`site` — what a domain entry would key on."""
        return normalize_host(self.site)

    @property
    def site_is_shared(self):
        return site_is_shared(self.site)

    @property
    def doaj_url(self):
        """This journal's page on doaj.org, so a row can be checked at the source."""
        return f"https://doaj.org/toc/{self.issns[0]}" if self.issns else ""

    def to_entry(self, *, include_site=False):
        """This journal as a whitelist entry, in ``user_lists.py``'s JSON format.

        Title and ISSNs always; the website only when asked for.  It is left out
        by default because the entry is meant to name *this journal*, and the
        matcher keys a domain on its host alone — so adding the site of a journal
        published on a platform silently widens the run's scope to every other
        journal on that platform.  See :func:`site_is_shared`.

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
    return Journal(
        title=title,
        issns=tuple(issns),
        site=(row.get(_COL_URL) or "").strip(),
        publisher=(row.get(_COL_PUBLISHER) or "").strip(),
        country=(row.get(_COL_COUNTRY) or "").strip(),
    )


def _read_cache():
    """The cached list, or ``None`` when it is absent, stale or unreadable."""
    try:
        if not CACHE_FILE.is_file():
            return None
        if time.time() - CACHE_FILE.stat().st_mtime > MAX_CACHE_AGE_SECONDS:
            return None
        with io.open(CACHE_FILE, encoding="utf-8") as fh:
            return [Journal(title=t, issns=tuple(i), site=s, publisher=p, country=c)
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
    """The best *limit* matches for *term*, most relevant first.

    *index* is what :func:`build_index` returned (a bare journal list is also
    accepted, and indexed on the spot).  Ranking is by how early and how cleanly
    the term appears, which is what makes a half-typed name useful:

      1. the title starts with it — "power sou" -> *Power Sources…*
      2. a word of the title starts with it — "sources" -> *Journal of Power Sources*
      3. it appears anywhere in the title
      4. it appears in the publisher's name

    An ISSN is matched exactly instead, against either of a journal's two.
    """
    term = (term or "").strip().lower()
    if not term or not index:
        return []
    if index and isinstance(index[0], Journal):
        index = build_index(index)

    issn = normalize_issn(term) if _ISSN_RE.search(term) else ""
    if issn:
        return [j for _h, _t, j in index if issn in j.issns][:limit]

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
        if len(hits) > 4000:            # a one-letter term matches thousands
            break
    hits.sort(key=lambda h: (h[0], h[1]))
    return [j for _r, _l, j in hits[:limit]]
