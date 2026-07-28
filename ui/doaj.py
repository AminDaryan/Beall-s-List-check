"""Look journals up in the DOAJ, to build the whitelist by name rather than by hand.

The Directory of Open Access Journals (https://doaj.org) is the reference index
of vetted open-access journals.  Step 3's **Only check these venues** list is a
*scope filter* — see ``user_lists.py`` — and typing a journal into it by hand
means knowing its exact title, its website, or its ISSN.  DOAJ knows all three,
so this module turns "the journal I mean is called roughly this" into an entry
the check can match on.

Nothing in the check engine imports this: DOAJ has no part in any verdict, and
must not acquire one.  Being indexed in DOAJ says a journal passed DOAJ's
editorial screening; it is not the question this tool asks, which is whether the
venue appears on Beall's List.  All this module does is help *name* a venue.

Like ``runners.py`` this is import-safe — no Streamlit — so it can be exercised
from a shell.  Failures are *returned*, never raised: the caller caches results,
and an exception cannot be cached, so a rejected query would otherwise be re-sent
to DOAJ on every rerun of the page.

    >>> search("journal of power sources advances").journals[0].issns
    ('2666-2485',)
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

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


_API = "https://doaj.org/api/search/journals/"

# DOAJ caps pageSize at 100 whatever is asked for; the UI wants far fewer than
# that on screen at once, so the ceiling is only here to keep a caller honest.
MAX_PAGE_SIZE = 100
DEFAULT_LIMIT = 8

# No key is needed for search, and DOAJ asks only that clients identify
# themselves.  Same shape as data_quality.py's Crossref agent.
_UA = "bealls-list-check/1.0 (research tooling; +https://doaj.org/docs/api)"

_ISSN_RE = re.compile(r"\b\d{4}-?\d{3}[\dxX]\b")
# Labels people put in front of a pasted ISSN, dropped before deciding whether
# the box holds nothing else.
_ISSN_LABEL_RE = re.compile(r"\b[ep]?issn\b:?", re.I)

# A field-scoped query is recognised narrowly, because the alternative is worse
# than not supporting one: "Nature: Communications" is a journal title, and read
# as a query scoped to a field named `Nature` it returns nothing at all rather
# than the twelve journals the escaped text finds.  So the colon must be followed
# immediately by a value (a title's colon is followed by a space) AND the field
# must look like a real one — dotted, as DOAJ's own paths are, or one of the
# documented shortcuts.
_FIELD_RE = re.compile(r"^([A-Za-z_][\w.]*):\S")
_KNOWN_FIELDS = {
    "issn", "eissn", "pissn", "title", "publisher", "subject", "license",
    "country", "language", "doi", "abstract", "keywords", "editor",
}

# Elasticsearch query_string reserves these.  An unescaped one is not a subtly
# worse search, it is an HTTP 400 — "Nature: Communications" returns nothing at
# all unescaped and twelve journals escaped — so everything the user types is
# escaped unless it is deliberately an advanced query (see :func:`build_query`).
_RESERVED = set('+-=&|><!(){}[]^"~*?:\\/')


def _escape(text):
    """Backslash-escape every character Elasticsearch would read as syntax."""
    return "".join("\\" + ch if ch in _RESERVED else ch for ch in text)


def build_query(text):
    """The DOAJ query for what the user typed, and how it was read.

    Returns ``(query, mode)`` where *mode* is one of:

      * ``issn``     — the box held nothing but ISSNs, so they are asked for by
                       identifier.  This is the exact lookup, and the one worth
                       recommending: it returns the journal or it returns nothing.
      * ``advanced`` — the text is already a field-scoped query
                       (``bibjson.publisher.name:Elsevier``), so it is passed
                       through untouched.  It may well be rejected by DOAJ; that
                       is DOAJ's answer to give, and refusing to send it would be
                       this module deciding what DOAJ supports on DOAJ's behalf.
      * ``text``     — anything else: escaped and sent as free text, which DOAJ
                       ranks by relevance across title, publisher and subject.
    """
    text = (text or "").strip()
    if not text:
        return "", "text"

    issns = _ISSN_RE.findall(text)
    leftover = _ISSN_LABEL_RE.sub("", _ISSN_RE.sub("", text)).strip(" ,;/|")
    if issns and not leftover:
        # Nothing in the box but ISSNs (and perhaps the word "ISSN") — ask by
        # identifier, OR-ing several so a journal's print and electronic ISSN
        # pasted together still find it once.
        return " OR ".join(f"issn:{normalize_issn(i)}" for i in issns), "issn"

    field = _FIELD_RE.match(text)
    if field and ("." in field.group(1) or field.group(1).lower() in _KNOWN_FIELDS):
        return text, "advanced"

    return _escape(text), "text"


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
    "dergipark.org.tr", "dergipark.gov.tr", "jstage.jst.go.jp", "scielo.br",
    "redalyc.org", "sanad.iau.ir", "digitalcommons.",
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
    """One DOAJ journal record, reduced to what naming a venue needs."""

    title: str
    issns: tuple = ()                 # print then electronic, normalized, deduped
    site: str = ""                    # bibjson.ref.journal — the journal's homepage
    publisher: str = ""
    country: str = ""
    subjects: tuple = ()
    doaj_id: str = ""

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
        if self.issns:
            return f"https://doaj.org/toc/{self.issns[0]}"
        return f"https://doaj.org/search/journals?source={self.doaj_id}" if self.doaj_id else ""

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


def _journal(record):
    """Build a :class:`Journal` from one DOAJ API result."""
    bibjson = record.get("bibjson") or {}
    issns = []
    for key in ("pissn", "eissn"):
        issn = normalize_issn(bibjson.get(key))
        if issn and issn not in issns:
            issns.append(issn)
    return Journal(
        title=(bibjson.get("title") or "").strip(),
        issns=tuple(issns),
        site=((bibjson.get("ref") or {}).get("journal") or "").strip(),
        publisher=((bibjson.get("publisher") or {}).get("name") or "").strip(),
        country=((bibjson.get("publisher") or {}).get("country") or "").strip(),
        subjects=tuple(s.get("term", "") for s in (bibjson.get("subject") or [])
                       if s.get("term")),
        doaj_id=record.get("id", ""),
    )


@dataclass(frozen=True)
class SearchResult:
    """What one search produced — including, on failure, why it produced nothing."""

    journals: tuple = ()
    total: int = 0                 # matches DOAJ holds, which exceeds len(journals)
    query: str = ""                # what was actually sent, after build_query
    mode: str = "text"
    error: str = ""                # a sentence for the user; "" when the search ran

    @property
    def ok(self):
        return not self.error


def search(text, *, limit=DEFAULT_LIMIT, timeout=12.0):
    """Search DOAJ's journals for *text*; never raises.

    *limit* is how many records to fetch, not how many exist — ``total`` reports
    the latter so the caller can say "8 of 43" rather than implying it has them
    all.

    Every failure comes back as :attr:`SearchResult.error`, phrased for the
    person who typed the query rather than for a log.  Callers are expected to
    memoize this function; returning failures makes them cacheable too, so a
    query DOAJ rejects is sent once rather than on every rerun of the page.
    """
    query, mode = build_query(text)
    if not query:
        return SearchResult(query="", mode=mode)

    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    # The query is a path SEGMENT, so `quote` has to encode "/" as well — a
    # journal title containing one would otherwise split the URL.
    url = f"{_API}{quote(query, safe='')}?page=1&pageSize={limit}"
    try:
        response = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    except requests.exceptions.Timeout:
        return SearchResult(query=query, mode=mode,
                            error=f"DOAJ did not answer within {timeout:g}s. "
                                  "It may be busy — try again, or add the journal by hand.")
    except requests.exceptions.RequestException:
        return SearchResult(query=query, mode=mode,
                            error="Could not reach DOAJ (no internet, or the site is down). "
                                  "Journals can still be added by hand.")

    if response.status_code == 400:
        # DOAJ rejected the query as syntax.  Only an `advanced` query can
        # normally get here — free text is escaped — so say which half is wrong.
        return SearchResult(
            query=query, mode=mode,
            error=("DOAJ could not parse that query. Field-scoped searches "
                   "(`name:value`) are passed through as typed, so check the field name — "
                   "or drop the colon to search as plain text."
                   if mode == "advanced" else
                   "DOAJ could not parse that query. Try a plainer search — "
                   "the journal's title, or its ISSN."))
    if response.status_code != 200:
        return SearchResult(query=query, mode=mode,
                            error=f"DOAJ answered {response.status_code}. Try again shortly.")

    try:
        payload = response.json()
    except ValueError:
        return SearchResult(query=query, mode=mode,
                            error="DOAJ's answer was not readable as JSON.")

    journals = tuple(_journal(r) for r in (payload.get("results") or []))
    # A record with neither a title nor an ISSN cannot be turned into a whitelist
    # entry that matches anything, so it is dropped rather than offered.
    journals = tuple(j for j in journals if j.title or j.issns)
    return SearchResult(journals=journals, total=int(payload.get("total") or 0),
                        query=query, mode=mode)
