"""Match a single paper's publication venue against the Beall's List snapshot.

This is the heart of the check.  Given the vendored snapshot and one Semantic
Scholar paper record, it decides one of:

  on_list   — the venue matches a Beall entry by a high-confidence signal
              (exact domain, exact ISSN, or exact normalized name)
  review    — only a softer signal matched (open-access-PDF domain, same
              registrable domain, or a fuzzy name match); a human should verify
  clean     — the venue was identified and did not match any entry
  no_venue  — preprint server or no venue info; not classifiable at all

Design choices that keep results *truthful*:
  * Signals are tried strongest-first and the first hit wins, so every row's
    ``matched_on`` says exactly *why* it was flagged.
  * Domain matching is preferred over name matching — it is far less ambiguous
    than fuzzy publisher names.
  * Preprint detection looks only at the *venue* domain/name, never at the
    open-access PDF host (published papers routinely have an arXiv PDF).
  * Nothing is asserted as ``on_list`` from a fuzzy match; fuzzy only ever
    yields ``review``.
"""

import sys
from pathlib import Path

# This module now lives in <pkg>/match/; put the package root and every foldered
# module (foo/foo.py) on the path so the flat 'from x import y' imports still work.
_ROOT = Path(__file__).resolve().parent.parent
for _p in [_ROOT, *(d for d in _ROOT.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from normalize import (
    normalize_name, normalize_host, normalize_issn,
    extract_issns, significant_tokens, similarity,
)
from config import (
    FUZZY_CUTOFF, MIN_NAME_LEN, FUZZY_LIST_SOURCES, WEAK_LIST_SOURCES,
    PREPRINT_DOMAINS, PREPRINT_NAMES, GENERIC_HOSTS,
    ARCHIVE_RESOLVER_DOMAINS, REPOSITORY_HOST_MARKERS,
)

# When several entries share a domain, prefer the more paper-relevant list.
# "blacklist" (user-supplied) outranks everything — the user explicitly asserted it.
_LIST_PRIORITY = {
    "blacklist": -1,
    "publishers": 0, "standalone_journal": 1, "hijacked": 2,
    "vanity_press": 3, "misleading_metric": 4,
}


def _best(entries):
    """Pick the most paper-relevant entry from a list of candidates."""
    return min(entries, key=lambda e: _LIST_PRIORITY.get(e["list_source"], 9))


def _is_non_publisher_host(host):
    """True if *host* is an archive / repository / generic platform, not a publisher.

    An empty host counts (the paper has no canonical venue of its own).  Used to
    explain an alternate-URL match: when the canonical venue is one of these, the
    alternate (the listed publisher) is most likely the paper's real venue rather
    than "a different journal".
    """
    if not host:
        return True
    if host in GENERIC_HOSTS or any(host.endswith("." + g) for g in GENERIC_HOSTS):
        return True
    if ".".join(host.split(".")[-2:]) in ARCHIVE_RESOLVER_DOMAINS:
        return True
    return any(marker in host for marker in REPOSITORY_HOST_MARKERS)


def _domain_corroborates(entry_domain, paper_domains):
    """True if any of the paper's venue hosts IS the listed entry's website.

    A name match is only high-confidence when the paper's own venue is hosted at
    the same site as the Beall entry.  Matches exactly, or as a subdomain in
    either direction (ojs.x.com vs x.com).
    """
    for d in paper_domains:
        if not d:
            continue
        if (d == entry_domain or d.endswith("." + entry_domain)
                or entry_domain.endswith("." + d)):
            return True
    return False


def venue_signals(paper):
    """Return ``(venue_domains, normalized_names)`` for whitelist scoping.

    venue_domains = the canonical + alternate *venue* hosts (NOT the open-access
    PDF host — a paper's PDF living on a publisher's server does not mean it
    belongs to that publisher).  Used by the whitelist filter in user_lists.py.
    """
    pv = paper.get("publicationVenue") or {}
    domains = ([normalize_host(pv["url"])] if pv.get("url") else []) + [
        normalize_host(u) for u in (pv.get("alternate_urls") or []) if u
    ]
    domains = [d for d in domains if d]
    names, seen = [], set()
    for n in [paper.get("venue"), pv.get("name")] + list(pv.get("alternate_names") or []):
        nn = normalize_name(n)
        if nn and nn not in seen:
            seen.add(nn)
            names.append(nn)
    return domains, names


class BeallIndex:
    """Indexes the snapshot entries for O(1) exact lookups + blocked fuzzy."""

    def __init__(self, entries):
        self.entries = entries
        self.by_domain = {}
        self.by_name = {}
        self.by_issn = {}
        self.by_token = {}      # significant token -> set of entry indices

        for idx, e in enumerate(entries):
            dom = e.get("domain", "")
            if dom and dom not in GENERIC_HOSTS:
                self.by_domain.setdefault(dom, []).append(e)
            nm = e.get("normalized_name", "")
            if nm:
                self.by_name.setdefault(nm, []).append(e)
            for issn in e.get("issns", []):
                self.by_issn.setdefault(normalize_issn(issn), []).append(e)
            if e["list_source"] in FUZZY_LIST_SOURCES:
                for tok in significant_tokens(nm):
                    self.by_token.setdefault(tok, set()).add(idx)

    # -- domain (exact host, or paper host is a subdomain of a listed host) --
    def domain_match(self, host):
        """Match a paper host against listed hosts, longest suffix first.

        Returns ``(entry, matched_on)`` where ``matched_on`` is ``"domain"``
        for an exact host match or ``"domain_subdomain"`` when the paper host
        is a subdomain of a listed host (e.g. ``ojs.bilpublishing.com`` ->
        listed ``bilpublishing.com``).

        Crucially, only *actual listed hosts* are ever used as keys, so a paper
        host like ``jurnal.unimed.ac.id`` can never collapse onto a public
        suffix such as ``ac.id`` — that whole class of false positives is gone
        by construction.
        """
        if not host:
            return None, None
        labels = host.split(".")
        for i in range(len(labels) - 1):
            cand = ".".join(labels[i:])
            if cand in self.by_domain:
                return _best(self.by_domain[cand]), (
                    "domain" if i == 0 else "domain_subdomain"
                )
        return None, None

    # -- fuzzy ------------------------------------------------------------
    def fuzzy_best(self, normalized_candidate):
        """Best (entry, score) for a candidate name via token-blocked difflib."""
        toks = significant_tokens(normalized_candidate)
        if not toks:
            return None, 0.0
        cand_idxs = set()
        for t in toks:
            cand_idxs |= self.by_token.get(t, set())
        best_entry, best_score = None, 0.0
        for i in cand_idxs:
            score = similarity(normalized_candidate, self.entries[i]["normalized_name"])
            if score > best_score:
                best_entry, best_score = self.entries[i], score
        return best_entry, best_score


def _paper_url(paper, ext):
    """Best link to the paper *at its publisher*.

    Prefers the DOI (which resolves to the publisher's landing page for the
    article), then the open-access PDF, then the Semantic Scholar page.
    """
    doi = ext.get("DOI") or ""
    if doi:
        return f"https://doi.org/{doi}"
    oa = paper.get("openAccessPdf") or {}
    return oa.get("url") or paper.get("url") or ""


def _collect_mentions(paper, pv):
    """Every NAMED venue tied to this paper that ALSO has a web link.

    The 'Mentioned in' column lists only venues a reviewer can click through to
    verify, so an entry is kept only when the record gives it a URL; bare names
    with no link (an abbreviation, an alternate name) are omitted.  Each entry
    shows the venue name and links to the venue's OWN site.  We emit one entry
    per distinct venue host — the canonical ``publicationVenue.url`` plus any
    ``alternate_urls`` — so a merged-venue record (Semantic Scholar fusing two
    same-named journals) shows every publisher site it points at.  The venue
    *type* is not prefixed here — it is constant for the record and already shown
    in the 'Semantic Scholar venue' column's "Type" field.

    Returns a list of ``(display_text, url)`` tuples (possibly empty).
    """
    name = (pv.get("name") or paper.get("venue") or "").strip()

    urls = ([pv["url"]] if pv.get("url") else []) + [
        u for u in (pv.get("alternate_urls") or []) if u
    ]
    entries, seen = [], set()
    for u in urls:
        host = normalize_host(u)
        if not host or host in seen:
            continue
        seen.add(host)
        entries.append((u, host))
    if not entries:
        return []

    multi = len(entries) > 1
    mentions = []
    for u, host in entries:
        shown = name or host
        # When several hosts share one name, append the host to tell them apart.
        text = f"{shown}  ({host})" if (multi and name) else shown
        mentions.append((text, u))
    return mentions


def _blank_result(paper, source_file):
    """Build the result skeleton with the venue fields echoed for auditing."""
    ext = paper.get("externalIds") or {}
    pv = paper.get("publicationVenue") or {}
    return {
        "source_file": source_file,
        "paperId": paper.get("paperId", ""),
        "title": paper.get("title", "") or "",
        "year": paper.get("year", "") or "",
        "doi": ext.get("DOI", "") or "",
        "paper_url": _paper_url(paper, ext),
        "venue": paper.get("venue", "") or "",
        "venue_type": pv.get("type", "") or "",
        "venue_domain": "",
        "venue_url": "",            # full URL behind venue_domain (so it can be a link)
        "canonical_domain": "",     # the paper's own publicationVenue.url host ("" if none)
        "issn": normalize_issn(pv.get("issn")) if pv.get("issn") else "",
        "mentions": _collect_mentions(paper, pv),
        # filled in by matching:
        "status": "clean",
        "list_source": "",
        "matched_name": "",
        "matched_url": "",
        "alt_source_urls": [],      # for an alternate-URL match: the paper's OWN alternate URL(s) that hit a listed site
        "matched_on": "",
        "review_reason": "",
        "authentic_url": "",        # for a hijacked match: the genuine journal's URL
        "authentic_domain": "",     #   "        "      : the genuine journal's host
        "cleared_reason": "",       # set if a hijacked-by-name flag was cleared to clean
    }


def _review_reason(r: dict) -> str:
    """Plain-English reason a row is 'review' rather than a definite 'on_list'.

    Empty for every other status (on_list is definite; clean/no_venue/error
    have nothing to explain here).
    """
    if r["status"] != "review":
        return ""
    mo = r["matched_on"]
    if mo == "domain_alternate_url":
        canon = r.get("canonical_domain") or ""
        pub = r.get("matched_name") or "a listed publisher"
        alts = r.get("alt_source_urls") or []
        # Name the EXACT Semantic Scholar alternate URL(s) that matched a listed site.
        alt_txt = f" — {'; '.join(alts)} — " if alts else " "
        if _is_non_publisher_host(canon):
            where = (f"its canonical venue ({canon})" if canon
                     else "no canonical venue website of its own")
            return (f"Matched via an ALTERNATE venue URL. The paper lists {where}, "
                    f"which is an archive/repository or generic host rather than a "
                    f"publisher — so the alternate URL{alt_txt}pointing to the listed "
                    f"{pub} is most likely this paper's actual venue. Still verify, but "
                    f"this is a stronger signal than a typical alternate-only match.")
        return (f"Matched only via an ALTERNATE venue URL, not the canonical one: the "
                f"paper's venue is {canon}, but Semantic Scholar also lists an "
                f"alternate URL for it{alt_txt}that points to the listed {pub}. Semantic "
                f"Scholar sometimes merges same-named journals, so that publisher may "
                f"belong to a different journal than this paper's — verify they match.")
    if mo == "domain_open_access_pdf":
        return (f"The journal itself was not found on Beall's List, but the paper's "
                f"free (open-access) PDF is hosted on the listed site '{r.get('matched_name')}'. "
                "A PDF can be re-hosted by someone other than the publisher, so this "
                "is flagged for a check rather than treated as proof — confirm where "
                "the paper was actually published.")
    if r["list_source"] == "hijacked" and mo.startswith("name"):
        # Name BOTH sides of the match explicitly — the paper's venue name and the
        # listed hijacked entry — and point to that entry's link (carried by the
        # 'Listed as' row above), so the reader sees exactly which matched which.
        clone_host = normalize_host(r.get("matched_url") or "")
        venue_name = r.get("venue") or "this paper's venue"
        listed_name = r.get("matched_name") or "a listed hijacked journal"
        site = f", whose counterfeit website is {clone_host}," if clone_host else ""
        reason = (f"This paper's venue name ('{venue_name}') matches the listed hijacked "
                  f"journal '{listed_name}'{site} on Beall's List (that entry is the "
                  f"link in the 'Listed as' row above). A hijacked journal is a "
                  f"counterfeit website that impersonates a real journal by copying its "
                  f"exact name, so a name match alone cannot tell the genuine journal "
                  f"from the counterfeit — this paper may well be in the genuine one.")
        # If the paper carries its OWN venue website and/or ISSN, neither could
        # have matched the counterfeit (a domain/ISSN hit would have asserted
        # on_list earlier), so they point at the genuine journal — surface that
        # mismatch so the reviewer (or the LLM pass) can resolve it fast.
        own = []
        if r.get("venue_domain"):
            own.append(f"its own website ({r['venue_domain']})")
        if r.get("issn"):
            own.append(f"ISSN {r['issn']}")
        if own:
            reason += f" Here the paper's venue gives {' and '.join(own)}"
            if clone_host:
                reason += (f", which differ from the counterfeit's website "
                           f"({clone_host}) on the list")
            reason += ", which suggests it is the genuine journal."
        reason += " Verify before excluding."
        return reason
    if mo == "name_exact":
        # Reached here only when the name matched but the paper's own URL did NOT
        # back it up (see _classify) — so it may be a different, same-named venue.
        listed_site = normalize_host(r.get("matched_url") or "")
        own = r.get("venue_domain") or ""
        where = f"this paper's journal is at {own}" if own else "this paper's venue has no such website"
        site = f" ({listed_site})" if listed_site else ""
        return (f"The venue NAME matches the listed '{r.get('matched_name')}', but "
                f"that listed venue's own website{site} is not where {where}. Many "
                "journals share a name, so this may be a different journal — verify "
                "it is really the listed one before excluding.")
    if mo == "name_fuzzy":
        return (f"The venue's name is a very close but inexact match (at least 93% "
                f"similar) to the listed '{r.get('matched_name')}' — for example a "
                "typo or spelling variant. Because it is not an exact match, it is "
                "flagged for a check rather than treated as confirmed.")
    if any(s in WEAK_LIST_SOURCES for s in r["list_source"].split("; ")):
        return (f"This matched '{r.get('matched_name')}' on one of Beall's WEAK "
                "lists — a vanity/book publisher or a company selling fake metrics, "
                "not a predatory journal as such. A match here is surfaced for a "
                "human to weigh; it is not a predatory-journal finding on its own.")
    return "This is a soft, lower-confidence match — verify it by hand before acting."


def out_of_scope_result(paper, source_file):
    """Result for a paper skipped because it isn't in the user's whitelist."""
    r = _blank_result(paper, source_file)
    r["status"] = "out_of_scope"
    return r


def match_paper(index: BeallIndex, paper: dict, source_file: str) -> dict:
    """Classify one paper. Never raises: a bad record yields an 'error' result.

    Wrapping the classification here means the batch driver in
    bealls_list_check.py stays a plain loop with no error bookkeeping, and the
    result schema lives in exactly one place (_blank_result).
    """
    r = _blank_result(paper, source_file)
    try:
        _classify(r, index, paper)
    except Exception as exc:  # one bad record must not kill a 22k-paper run
        r["status"] = "error"
        r["matched_on"] = f"{type(exc).__name__}: {exc}"
        return r
    r["review_reason"] = _review_reason(r)
    return r


def _classify(r: dict, index: BeallIndex, paper: dict) -> dict:
    """Run the matching tiers against a prebuilt result skeleton ``r``."""
    pv = paper.get("publicationVenue") or {}

    # ── candidate signals ────────────────────────────────────────────────
    # Semantic Scholar sometimes MERGES several same-named journals into one
    # venue record, so alternate_urls can point at unrelated publishers (a
    # legit Elsevier journal + a predatory clone, say).  We therefore separate
    # the canonical venue URL (publicationVenue.url) from the alternate URLs and
    # trust them differently: a canonical-domain match is asserted (on_list),
    # but an alternate-only domain match is merely flagged for review.
    canonical_domain = normalize_host(pv.get("url")) if pv.get("url") else ""
    alt_domains = [normalize_host(u) for u in (pv.get("alternate_urls") or []) if u]
    alt_domains = [d for d in alt_domains if d]
    all_domains = ([canonical_domain] if canonical_domain else []) + alt_domains
    r["venue_domain"] = canonical_domain or (alt_domains[0] if alt_domains else "")
    r["canonical_domain"] = canonical_domain
    # Full URL behind venue_domain, so the Domain cell can link to the venue site.
    if canonical_domain:
        r["venue_url"] = pv.get("url") or ""
    elif alt_domains:
        r["venue_url"] = next((u for u in (pv.get("alternate_urls") or [])
                               if normalize_host(u) == alt_domains[0]), "")

    oa = paper.get("openAccessPdf") or {}
    oa_domain = normalize_host(oa.get("url")) if oa.get("url") else ""

    name_sources = [paper.get("venue"), pv.get("name")] + list(
        pv.get("alternate_names") or []
    )
    norm_names, seen = [], set()
    for n in name_sources:
        nn = normalize_name(n)
        if nn and nn not in seen:
            seen.add(nn)
            norm_names.append(nn)

    issns = extract_issns(pv.get("issn"))

    # ── no-venue / preprint classification (venue signals only) ──────────
    is_preprint = any(d in PREPRINT_DOMAINS for d in all_domains) or (
        not all_domains and any(n in PREPRINT_NAMES for n in norm_names)
    )
    if is_preprint:
        r["status"] = "no_venue"
        return r
    if not all_domains and not norm_names and not issns:
        r["status"] = "no_venue"
        return r

    # ── high-confidence signals (asserted as on_list) ────────────────────
    # Canonical venue domain: exact host or subdomain of a listed host
    # (e.g. ojs.bilpublishing.com -> bilpublishing.com).
    if canonical_domain:
        entry, matched_on = index.domain_match(canonical_domain)
        if entry:
            return _hit(r, entry, "on_list", matched_on)

    for issn in issns:
        if issn in index.by_issn:
            return _hit(r, _best(index.by_issn[issn]), "on_list", "issn")

    for nm in norm_names:
        # Require >= 2 words: single generic words ("Research", "Heliyon")
        # collide with legitimate journals and cause false positives.
        if len(nm) >= MIN_NAME_LEN and len(nm.split()) >= 2 and nm in index.by_name:
            entry = _best(index.by_name[nm])
            # A name alone is high-confidence only if the paper's OWN venue URL
            # backs it up.  If the listed entry has a website and NONE of the
            # paper's URLs are that site, the name may just be a coincidence
            # (many journals share a name) -> downgrade to review, not on_list.
            status = "on_list"
            if entry.get("domain") and not _domain_corroborates(entry["domain"], all_domains):
                status = "review"
            return _hit(r, entry, status, "name_exact")

    # ── softer signals (flagged for review only) ─────────────────────────
    # One or more ALTERNATE venue URLs point at listed domains.  Because S2
    # merges same-named journals, an alternate may be a different journal than
    # the paper's, so this is review-only.  We collect EVERY listed publisher
    # among the alternates (not just the first) so a reviewer sees all the
    # flagged candidates at once.
    # Iterate the raw alternate URLs (not just their hosts) so we can show the
    # reviewer the EXACT Semantic Scholar URL that triggered the flag.
    alt_hits, alt_urls, seen = [], [], set()
    for u in (pv.get("alternate_urls") or []):
        dom = normalize_host(u) if u else ""
        if not dom:
            continue
        entry, _ = index.domain_match(dom)
        if entry and entry["name"] not in seen:
            seen.add(entry["name"])
            alt_hits.append(entry)
            alt_urls.append(u)
    if alt_hits:
        r["status"] = "review"
        r["matched_on"] = "domain_alternate_url"
        r["list_source"] = "; ".join(dict.fromkeys(e["list_source"] for e in alt_hits))
        r["matched_name"] = "; ".join(e["name"] for e in alt_hits)
        r["matched_url"] = alt_hits[0]["url"]
        r["alt_source_urls"] = alt_urls
        return r

    # The open-access PDF is hosted on a listed publisher domain, but the
    # venue itself was not identified as such — worth a human glance.
    if oa_domain:
        entry, _ = index.domain_match(oa_domain)
        if entry:
            return _hit(r, entry, "review", "domain_open_access_pdf")

    best_entry, best_score = None, 0.0
    for nm in norm_names:
        e, s = index.fuzzy_best(nm)
        if s > best_score:
            best_entry, best_score = e, s
    if best_entry and best_score >= FUZZY_CUTOFF:
        return _hit(r, best_entry, "review", "name_fuzzy")

    # ── nothing matched ──────────────────────────────────────────────────
    return r


def _hit(r, entry, status, matched_on):
    """Populate the match fields of a result dict from a snapshot entry.

    Two on_list -> review downgrades happen here so the matcher above can stay
    simple:
      * "weak" lists (vanity-press book publishers, fake-metric companies):
        surfaced for a human but never a high-confidence predatory verdict.
      * a NAME match against a "hijacked" entry: the predatory clone reuses the
        authentic journal's name, so an exact-name hit may actually be the
        legitimate journal.  Only a domain/ISSN hit (the clone's own identity)
        is asserted as on_list.
    """
    if status == "on_list" and entry["list_source"] in WEAK_LIST_SOURCES:
        status = "review"
    if (status == "on_list" and entry["list_source"] == "hijacked"
            and matched_on.startswith("name")):
        status = "review"
    r["status"] = status
    r["list_source"] = entry["list_source"]
    r["matched_name"] = entry["name"]
    r["matched_url"] = entry["url"]
    r["matched_on"] = matched_on
    r["authentic_url"] = entry.get("authentic_url", "")
    r["authentic_domain"] = entry.get("authentic_domain", "")
    return r
