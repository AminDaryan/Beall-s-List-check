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

Import-safe: no Streamlit, no network.
"""

import re
import sys
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
