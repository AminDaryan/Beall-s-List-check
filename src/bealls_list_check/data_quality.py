"""Pre-flight data-quality checks on the Semantic Scholar (S2) records.

S2 metadata is sometimes wrong, and a wrong verdict is worse than a flagged one.
These checks surface *suspect* records so they aren't trusted blindly.  Two
layers, combined by :func:`validate`:

  * :func:`offline_flags` — structural + internal-consistency checks (no network):
    missing venue metadata, malformed DOI/ISSN, encoding artifacts, and the
    "merged venues" red flag (S2 sometimes fuses two same-named journals, so its
    venue URLs span multiple publishers).

  * :func:`crossref_flags` — cross-check the DOI against Crossref, the
    publisher-deposited registration metadata, to catch genuinely wrong venue /
    ISSN.  Network; free; no key.  Bounded by DOI coverage (preprints/books have
    none).

An empty result means "nothing suspicious found" — NOT a guarantee of
correctness (S2 can be wrong in ways no automated check can see).
"""

import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import normalize_name, normalize_host, normalize_issn, significant_tokens
from config import (
    ARCHIVE_RESOLVER_DOMAINS, AGGREGATOR_HOST_MARKERS, SAME_PUBLISHER_DOMAINS,
    PREPRINT_DOMAINS,
)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ENTITY_RE = re.compile(r"&[A-Za-z]+;|&#\d+;|�")        # &amp; &#39; or U+FFFD
_CROSSREF_URL = "https://api.crossref.org/works/"
_UA = "bealls-data-quality/1.0 (research tooling)"

_crossref_cache = {}        # doi -> Crossref "message" dict, or None


# Second-level labels that are themselves public suffixes under a country-code
# TLD, so the registrable domain is the THIRD-from-last label (revistas.ucr.ac.cr
# -> ucr.ac.cr, NOT ac.cr).  Covers the academic/government multi-level domains
# common in scholarly venue URLs; not a full Public Suffix List, but enough to
# stop public suffixes (ac.cr, edu.ar, gob.es) being mistaken for "publishers".
_MULTI_LEVEL_SLD = {
    "ac", "co", "com", "edu", "ed", "go", "gob", "gov", "net", "or", "org",
    "mil", "ne", "res", "sa", "gr", "in",
}


def _registrable(host):
    """Best-effort eTLD+1, aware of multi-level country suffixes (ac.cr, edu.ar,
    gob.es, co.uk, …) so an institutional host isn't collapsed onto a public
    suffix and counted as a bogus 'publisher'."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Multi-level ccTLD: 2-letter country code + a known public second level
    # (ac/edu/gob/…) -> the registrable domain is the last THREE labels.
    if len(parts[-1]) == 2 and parts[-2] in _MULTI_LEVEL_SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def publisher_domains(paper):
    """Distinct *publisher* domains for a venue record.

    Gathers the canonical + alternate venue hosts, then keeps only genuine
    publishers by:
      * dropping aggregators / indexes / databases / reference sites that list
        journals but don't publish them (Gale, Wikipedia, PubMed, SciELO, …);
      * dropping archive / preservation / resolver services and preprint servers;
      * collapsing a publisher's several brand domains to one (ScienceDirect ->
        Elsevier, BMC/SpringerOpen -> Springer, …) so one company isn't counted
        as several publishers.
    Two or more remaining is the "merged venue" red flag.
    """
    pv = paper.get("publicationVenue") or {}
    hosts = [normalize_host(pv["url"])] if pv.get("url") else []
    hosts += [normalize_host(u) for u in (pv.get("alternate_urls") or []) if u]
    pubs = set()
    for h in hosts:
        if not h or any(m in h for m in AGGREGATOR_HOST_MARKERS):
            continue
        reg = _registrable(h)
        if reg in ARCHIVE_RESOLVER_DOMAINS or reg in PREPRINT_DOMAINS:
            continue
        pubs.add(SAME_PUBLISHER_DOMAINS.get(reg, reg))   # collapse same-publisher brands
    return pubs


def offline_flags(paper):
    """Structural + consistency warnings for one S2 record (no network)."""
    flags = []
    pv = paper.get("publicationVenue") or {}
    ext = paper.get("externalIds") or {}
    venue = paper.get("venue") or ""
    pv_name = pv.get("name") or ""
    issn_raw = pv.get("issn") or ""
    doi = (ext.get("DOI") or "").strip()

    if not (venue or pv_name or pv.get("url") or issn_raw):
        flags.append("No journal/venue information at all (so the venue can't be checked).")
    if doi and not _DOI_RE.match(doi):
        flags.append(f"The DOI doesn't look like a valid DOI: {doi!r}.")
    if issn_raw and not normalize_issn(issn_raw):
        flags.append(f"The ISSN isn't in the standard NNNN-NNNX form: {issn_raw!r}.")
    for label, val in (("venue name", venue), ("publication-venue name", pv_name)):
        if val and _ENTITY_RE.search(val):
            flags.append(f"The {label} contains leftover HTML codes (e.g. &amp;): {val!r}.")

    # Merged-venue red flag: the one venue record points at >1 publisher's website,
    # which usually means Semantic Scholar fused two same-named journals into one.
    publisher_regs = publisher_domains(paper)
    if len(publisher_regs) >= 2:
        flags.append(
            f"Semantic Scholar lists web addresses from {len(publisher_regs)} different "
            f"publishers for this one journal ({', '.join(sorted(publisher_regs))}). That "
            "usually means it has merged two different journals that share a name, so the "
            "venue shown for this paper may be the wrong one — worth checking by hand.")
    return flags


def _crossref_lookup(doi, *, timeout=10):
    if doi in _crossref_cache:
        return _crossref_cache[doi]
    message = None
    try:
        resp = requests.get(_CROSSREF_URL + requests.utils.quote(doi, safe=""),
                            headers={"User-Agent": _UA}, timeout=timeout)
        if resp.status_code == 200:
            message = resp.json().get("message")
    except (requests.RequestException, ValueError):
        message = None
    _crossref_cache[doi] = message
    return message


def crossref_flags(paper, *, timeout=10):
    """Warnings from comparing S2's venue/ISSN to Crossref's (network)."""
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").strip()
    if not doi or not _DOI_RE.match(doi):
        return []
    msg = _crossref_lookup(doi, timeout=timeout)
    if msg is None:
        return [f"DOI not found in Crossref (or lookup failed): {doi}."]

    pv = paper.get("publicationVenue") or {}
    s2_names = [paper.get("venue"), pv.get("name")] + list(pv.get("alternate_names") or [])
    cr_titles = msg.get("container-title") or []

    flags = []
    s2_tokens = set().union(*(significant_tokens(normalize_name(n)) for n in s2_names)) \
        if s2_names else set()
    cr_tokens = set().union(*(significant_tokens(normalize_name(t)) for t in cr_titles)) \
        if cr_titles else set()
    # Disagree only when neither side shares a meaningful word (tolerates
    # abbreviations like "WSEAS Trans. Inf. Sci." vs the full Crossref title).
    if s2_tokens and cr_tokens and not (s2_tokens & cr_tokens):
        s2_show = next((n for n in s2_names if n), "?")
        flags.append(f"Venue disagrees with Crossref — Semantic Scholar: {s2_show!r}; "
                     f"Crossref: {cr_titles[0]!r}.")

    s2_issn = normalize_issn(pv.get("issn")) if pv.get("issn") else ""
    cr_issns = {normalize_issn(i) for i in (msg.get("ISSN") or []) if normalize_issn(i)}
    if s2_issn and cr_issns and s2_issn not in cr_issns:
        flags.append(f"ISSN disagrees with Crossref — Semantic Scholar: {s2_issn}; "
                     f"Crossref: {sorted(cr_issns)}.")
    return flags


# Note: these venue checks are invoked through the single shared validator,
# common.input_quality.validate_input(), which all checks use.
