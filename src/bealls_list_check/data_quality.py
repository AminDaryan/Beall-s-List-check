"""Pre-flight data-quality checks on the Semantic Scholar (S2) records.

S2 metadata is sometimes wrong, and a wrong verdict is worse than a flagged one.
These checks surface *suspect* records so they aren't trusted blindly.  Two
layers, combined by :func:`validate`:

  * :func:`offline_flags` — structural + internal-consistency checks, offline
    except for one Crossref lookup on a "merged venue": missing venue metadata,
    malformed DOI/ISSN, encoding artifacts, and the "merged venues" red flag (S2
    sometimes fuses two same-named journals, so its venue URLs span multiple
    publishers).  When that flag fires, the paper's DOI is resolved via Crossref
    to name the journal it was really published in (see _resolve_real_journal).

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


def _classify_venue_hosts(paper):
    """Split a venue record's websites into genuine publishers vs the non-publisher
    hosts set aside before the merged-venue tally.

    Gathers the canonical + alternate venue hosts (deduped), then for each decides:
      * dropped — aggregators / indexes / databases / reference sites that list
        journals but don't publish them (Gale, Wikipedia, PubMed, SciELO, …),
        archive / preservation / resolver services, or preprint servers;
      * kept — a genuine publisher, reduced to its registrable domain and with a
        publisher's several brand domains collapsed to one (ScienceDirect ->
        Elsevier, BMC/SpringerOpen -> Springer, …) so one company isn't counted
        as several publishers.

    Returns ``(publishers, dropped)``: *publishers* is the set of kept domains
    (two or more is the "merged venue" red flag); *dropped* is a list of
    ``(host, reason)`` in input order, so a message can name exactly which links
    were excluded and why — the reasons are plural-neutral phrases.
    """
    pv = paper.get("publicationVenue") or {}
    hosts = [normalize_host(pv["url"])] if pv.get("url") else []
    hosts += [normalize_host(u) for u in (pv.get("alternate_urls") or []) if u]
    publishers, dropped, seen = set(), [], set()
    for h in hosts:
        if not h or h in seen:
            continue
        seen.add(h)
        if any(m in h for m in AGGREGATOR_HOST_MARKERS):
            dropped.append((h, "indexes or aggregators, not publishers"))
            continue
        reg = _registrable(h)
        if reg in ARCHIVE_RESOLVER_DOMAINS:
            dropped.append((h, "library archives or identifier resolvers, not publishers"))
            continue
        if reg in PREPRINT_DOMAINS:
            dropped.append((h, "preprint servers, not publishers"))
            continue
        publishers.add(SAME_PUBLISHER_DOMAINS.get(reg, reg))   # collapse same-publisher brands
    return publishers, dropped


def publisher_domains(paper):
    """Distinct *publisher* domains for a venue record (see _classify_venue_hosts).

    Two or more is the "merged venue" red flag.
    """
    return _classify_venue_hosts(paper)[0]


def _resolve_real_journal(paper, publisher_regs):
    """Name the journal a paper was really published in (from Crossref via its DOI),
    AND say explicitly which of the merged publishers' sites is the correct one and
    which are wrong.

    Used only for a merged-venue paper, to answer "which of the merged journals is
    this paper's?" automatically instead of asking the reader to check the DOI by
    hand.  When Crossref's publisher website is one of the merged sites, the message
    states that site as CORRECT and names the rest as WRONG, so the reader is never
    left to work out the mapping themselves; when it is none of them, it says so
    plainly.  Returns a finished sentence, or "" when there is no usable DOI or
    Crossref record (the caller then falls back to the check-by-hand wording).
    Makes one (cached) Crossref lookup.
    """
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").strip()
    if not doi or not _DOI_RE.match(doi):
        return ""
    msg = _crossref_lookup(doi)
    if not msg:
        return ""
    titles = msg.get("container-title") or []
    journal = titles[0] if titles else ""
    if not journal:
        return ""
    issns = sorted({normalize_issn(i) for i in (msg.get("ISSN") or []) if normalize_issn(i)})
    publisher = (msg.get("publisher") or "").strip()
    detail = ([f"ISSN {', '.join(issns)}"] if issns else []) + ([publisher] if publisher else [])
    named = f"{journal} ({'; '.join(detail)})" if detail else journal
    base = (f"According to Crossref (the publisher registry), this paper was actually "
            f"published in {named}. ")

    # Tie Crossref's real publisher site back to one of the merged sites (reducing it
    # to the same registrable + same-publisher-collapsed form publisher_domains uses),
    # so we can name which site is right and which were wrongly merged in.
    cr_url = (((msg.get("resource") or {}).get("primary") or {}).get("URL") or "")
    cr_host = normalize_host(cr_url)
    cr_reg = SAME_PUBLISHER_DOMAINS.get(_registrable(cr_host), _registrable(cr_host)) if cr_host else ""

    if cr_reg and cr_reg in publisher_regs:
        wrong = sorted(d for d in publisher_regs if d != cr_reg)
        wrong_txt = (f"'{wrong[0]}' is WRONG for this paper (it belongs to a different "
                     f"journal that merely shares the name)"
                     if len(wrong) == 1 else
                     f"the others — {', '.join(wrong)} — are WRONG for this paper (they "
                     f"belong to different journals that merely share the name)")
        return (base + f"So of the {len(publisher_regs)} merged sites, '{cr_reg}' is the "
                f"CORRECT venue for this paper and {wrong_txt}. Treat '{cr_reg}' as the "
                f"venue and ignore the rest.")
    # Crossref's publisher site is none of the merged ones.
    where = f"'{cr_host}'" if cr_host else "a site Crossref does not name"
    return (base + f"Its publisher website ({where}) is NONE of the merged sites "
            f"({', '.join(sorted(publisher_regs))}), so none of those is this paper's "
            f"venue — treat the Crossref journal named above as the venue.")


def offline_flags(paper):
    """Structural + consistency warnings for one S2 record.

    Offline, with ONE exception: when the "merged venue" red flag fires, this
    makes a single (cached) Crossref lookup to name the paper's real journal (see
    _resolve_real_journal), because that question is far more useful answered than
    left to the reader.  Every other check here is purely offline.
    """
    flags = []
    pv = paper.get("publicationVenue") or {}
    ext = paper.get("externalIds") or {}
    venue = paper.get("venue") or ""
    pv_name = pv.get("name") or ""
    issn_raw = pv.get("issn") or ""
    doi = (ext.get("DOI") or "").strip()

    if not (venue or pv_name or pv.get("url") or issn_raw):
        flags.append("This record has no journal or venue information at all (no name, "
                     "website or ISSN), so its venue cannot be checked against the list.")
    if doi and not _DOI_RE.match(doi):
        flags.append(f"The DOI is malformed — it is not in the standard 10.NNNN/... "
                     f"form: {doi!r}.")
    if issn_raw and not normalize_issn(issn_raw):
        flags.append(f"The ISSN is malformed — it is not in the standard NNNN-NNNX "
                     f"form (eight digits, e.g. 0079-2047): {issn_raw!r}.")
    for label, val in (("venue name", venue), ("publication-venue name", pv_name)):
        if val and _ENTITY_RE.search(val):
            flags.append(f"The {label} contains leftover HTML character codes (e.g. "
                         f"'&amp;' instead of '&'), a sign of an encoding error in "
                         f"the source data: {val!r}.")

    # Merged-venue red flag: the one venue record points at >1 publisher's website,
    # which usually means Semantic Scholar fused two same-named journals into one.
    publisher_regs, dropped = _classify_venue_hosts(paper)
    if len(publisher_regs) >= 2:
        n = len(publisher_regs)
        msg = (f"This paper's Semantic Scholar venue record points to {n} different "
               f"publishers' websites ({', '.join(sorted(publisher_regs))}). A single "
               f"journal has one publisher, so Semantic Scholar has probably combined "
               f"{n} different journals that share the same name into one record. ")
        # Name any other linked sites that were set aside, so this never looks
        # inconsistent with the (more inclusive) 'Mentioned in' column.
        if dropped:
            groups = {}
            for h, why in dropped:
                groups.setdefault(why, []).append(h)
            parts = "; ".join(f"{', '.join(hs)} — {why}" for why, hs in groups.items())
            if len(dropped) == 1:
                msg += (f"(One other linked site does not count as a separate journal: "
                        f"{parts}.) ")
            else:
                msg += (f"({len(dropped)} other linked sites do not count as separate "
                        f"journals: {parts}.) ")
        msg += _resolve_real_journal(paper, publisher_regs) or (
            "The journal it reports for this paper may therefore be one of the others "
            "rather than the correct one — open the paper's DOI to confirm which journal "
            "it was actually published in.")
        flags.append(msg)
    return flags


# One reused HTTP session so the many sequential Crossref lookups (one per
# merged-venue paper) share a keep-alive connection instead of reconnecting each time.
_session = requests.Session()
_session.headers.update({"User-Agent": _UA})


def _crossref_lookup(doi, *, timeout=10):
    if doi in _crossref_cache:
        return _crossref_cache[doi]
    message = None
    try:
        resp = _session.get(_CROSSREF_URL + requests.utils.quote(doi, safe=""),
                            timeout=timeout)
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
        return [f"This DOI was not found in Crossref, the publisher registry (or the "
                f"lookup failed), so the venue could not be cross-checked: {doi}."]

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
        flags.append(f"The venue name disagrees with Crossref's record for this DOI, so "
                     f"Semantic Scholar may have the wrong venue — Semantic Scholar says "
                     f"{s2_show!r}; Crossref says {cr_titles[0]!r}.")

    s2_issn = normalize_issn(pv.get("issn")) if pv.get("issn") else ""
    cr_issns = {normalize_issn(i) for i in (msg.get("ISSN") or []) if normalize_issn(i)}
    if s2_issn and cr_issns and s2_issn not in cr_issns:
        flags.append(f"The ISSN disagrees with Crossref's record for this DOI, so Semantic "
                     f"Scholar may have the wrong venue — Semantic Scholar says {s2_issn}; "
                     f"Crossref says {sorted(cr_issns)}.")
    return flags


# Note: these venue checks are invoked through the single shared validator,
# common.input_quality.validate_input(), which all checks use.
