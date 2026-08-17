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
    extract_issns, significant_tokens, similarity, split_list_sources,
)
from config import (
    FUZZY_CUTOFF, MIN_NAME_LEN, FUZZY_LIST_SOURCES, WEAK_LIST_SOURCES,
    PREPRINT_DOMAINS, PREPRINT_NAMES, GENERIC_HOSTS,
    ARCHIVE_RESOLVER_DOMAINS, REPOSITORY_HOST_MARKERS, PROVIDER_RANK,
)

# When several entries share a domain, prefer the more paper-relevant list.
# "blacklist" (user-supplied) outranks everything — the user explicitly asserted it.
_LIST_PRIORITY = {
    "blacklist": -1,
    "publishers": 0, "standalone_journal": 1, "hijacked": 2,
    "vanity_press": 3, "misleading_metric": 4,
}


def _best(entries):
    """Pick the most paper-relevant entry from a list of candidates.

    Ranked by list category first, then by provider, so the winner does not
    depend on the order the snapshots happened to be concatenated in — which is
    what ``min()``'s stability alone gave, and which becomes visible as soon as
    two catalogues list the same venue.
    """
    return min(entries, key=lambda e: (_LIST_PRIORITY.get(e["list_source"], 9),
                                       PROVIDER_RANK.get(e.get("provider"), 9)))


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
    """Return ``(venue_domains, normalized_names, issns)`` for whitelist scoping.

    venue_domains = the canonical + alternate *venue* hosts (NOT the open-access
    PDF host — a paper's PDF living on a publisher's server does not mean it
    belongs to that publisher).  Used by the whitelist filter in user_lists.py.

    The ISSNs come from ``publicationVenue.issn`` via the same
    :func:`extract_issns` that ``_classify`` uses below, so the whitelist and the
    Beall index ask the identity question of a venue in exactly one way.  It is
    the most precise signal of the three: a name can be shared by unrelated
    journals and a domain can be a publisher platform hosting thousands, while an
    ISSN names one journal and nothing else.
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
    return domains, names, extract_issns(pv.get("issn"))


def user_entry_signals(entry):
    """Which signals one of *the user's* entries may be matched on.

    An entry is matched on **the most precise identifier it carries** — and, when
    that identifier is an ISSN, on an exact whole-name match as well.  Returns ()
    for a Beall entry, which this rule does not govern.

    The precision half of the rule earns its keep.  Choosing *Neural Networks*
    out of DOAJ adds the journal's official title and its ISSN, and a title like
    that is a phrase hundreds of venues contain: measured over the corpora in
    ``docs/``, matching that entry by phrase reaches 114 papers across 11 venues,
    94 of them in venues nobody named — a BlackboxNLP workshop, *IEEE
    Transactions on Neural Networks and Learning Systems*, four conferences.  By
    ISSN it reaches the 20 papers in the journal.

    An ISSN is to a journal what a DOI is to an article: a registered identifier
    for one thing.  A name is a description, and descriptions are shared.

    **But the paper has to carry the ISSN for that to help, and 42% of the
    records in those same corpora do not.**  A record naming *Neural Networks*
    with no ISSN field is in the journal the user named, and dropping it is the
    kind of miss nobody can see.  So an ISSN entry also matches a venue whose
    name is *exactly* its own — which is not what caused the over-matching.
    Phrase containment was: of the 94 unwanted papers, none is in a venue named
    exactly "Neural Networks", and 83 of them carry no ISSN, so an exact-name
    fallback recovers the missing records without letting any of them back in.

    Fuzzy near-name matching stays off for these entries for the same reason: a
    title taken from a directory is not a guess in need of correcting.
    """
    if entry.get("list_source") not in ("whitelist", "blacklist"):
        return ()
    if entry.get("issns"):
        return ("issn", "name_exact")
    if entry.get("domain"):
        return ("domain",)
    # A name typed by hand is the wide net, and meant to be: it matches as a
    # whole phrase inside a venue's name, which is what "IEEE" is for.
    return ("name_phrase",) if entry.get("normalized_name") else ()


class BeallIndex:
    """Indexes the snapshot entries for O(1) exact lookups + blocked fuzzy.

    The user's own blacklist entries are indexed a second time, on their own, as
    ``user_index``.  They are in ``entries`` as well and match there exactly as
    Beall's do — this second, smaller index exists only so that
    :func:`match_paper` can ask *"did the person running this check name this
    venue?"* before it asks any of its own questions.  See the blacklist block
    there for why that order matters.
    """

    def __init__(self, entries, _sub=False):
        self.entries = entries
        self.user_index = None
        if not _sub:
            user = [e for e in entries if e.get("list_source") == "blacklist"]
            if user:
                self.user_index = BeallIndex(user, _sub=True)
        self.by_domain = {}
        self.by_name = {}
        self.by_issn = {}
        self.by_token = {}      # significant token -> set of entry indices

        for idx, e in enumerate(entries):
            # () for every Beall entry, which is governed by the tiers below
            # rather than by this rule — so `not signals` means "index it all".
            signals = user_entry_signals(e)
            dom = e.get("domain", "")
            if dom and dom not in GENERIC_HOSTS and (not signals or "domain" in signals):
                self.by_domain.setdefault(dom, []).append(e)
            nm = e.get("normalized_name", "")
            if nm and (not signals or {"name_exact", "name_phrase"} & set(signals)):
                self.by_name.setdefault(nm, []).append(e)
            if not signals or "issn" in signals:
                for issn in e.get("issns", []):
                    self.by_issn.setdefault(normalize_issn(issn), []).append(e)
            if e["list_source"] in FUZZY_LIST_SOURCES and (not signals
                                                           or "name_phrase" in signals):
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
        "provider": "",             # which catalogue the match came from ("beall", "user", ...)
        "matched_entry": None,      # the entry object that decided the verdict (not rendered)
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
    # A 'review' can name several lists at once (the alternate-URL tier joins
    # them), so read the field through the shared splitter rather than comparing
    # it whole.  The two questions asked below are deliberately different:
    # *primary* is the list of the entry this row's link points at — the one the
    # hijacked wording describes — while the weak-list test further down asks
    # whether ANY matched list is a weak one, because that is a property of the
    # match as a whole.
    sources = split_list_sources(r["list_source"])
    primary = sources[0] if sources else ""
    if mo == "name_exact_issn_differs":
        listed = r.get("matched_name") or "the entry you listed"
        return (f"You put '{listed}' on the blacklist for this run, and this venue's "
                f"name is exactly that — but the paper carries ISSN {r.get('issn') or '?'}, "
                "which is not the ISSN on your entry. That is either the same journal "
                "under its other registered number (print and online hold one each, and "
                "a directory often gives only one), or a different journal with the same "
                "title. Open the venue to tell which; the run will not assert it either "
                "way. Adding the second ISSN to your entry settles it for good.")
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
    if primary == "hijacked" and mo.startswith("name"):
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
    if any(s in WEAK_LIST_SOURCES for s in sources):
        # Names the category rather than the catalogue, and never says "Beall's":
        # the weak categories now include a conference list and an unclassified
        # one that no Beall list has, so a Kscien conference match was being
        # reported as a finding on "one of Beall's WEAK lists".
        weak = [s for s in sources if s in WEAK_LIST_SOURCES]
        what = {
            "vanity_press": "a book/monograph publisher rather than a journal one",
            "misleading_metric": "a company selling fake metrics, not a venue at all",
            "predatory_conference": "a conference series rather than a journal",
            "unclassified": ("a venue the list published without saying which of its "
                             "own lists it belongs to"),
        }.get(weak[0], "not a predatory-journal signal on its own")
        return (f"This matched '{r.get('matched_name')}' on a WEAK list — {what}. "
                "A paper is published in a journal, so a match here is surfaced for "
                "a human to weigh rather than treated as a predatory-journal "
                "finding on its own.")
    return "This is a soft, lower-confidence match — verify it by hand before acting."


def _user_blacklist_hit(user_index, canonical_domain, all_domains, norm_names, issns):
    """``(entry, matched_on, status)`` for the user's blacklist, or None.

    The status is ``on_list`` except where the paper contradicts the entry it
    matched — see the exact-name branch below.

    The same three exact signals the venue box promises on each row it shows —
    ISSN, the site or a site under it, the name — tried strongest first, so the
    ``matched_on`` recorded here reads the same as any other row's and the
    Summary sheet's breakdown by signal keeps counting them together.

    Every venue domain is offered, not only the canonical one: a blacklist is
    the user saying *this publisher*, and a record whose canonical URL is
    elsewhere but whose alternate points at the named site is exactly the case
    they mean to catch.

    **A name matches as a whole phrase inside the venue's name**, not only as
    the whole of it — the rule the whitelist has always used, and the one the
    row's own label promises ("papers whose venue name contains this name as a
    whole word").  Exact equality was the rule here and it is not one a real
    catalogue survives: Semantic Scholar records that journal as *ACM
    Transactions on Multimedia Computing, Communications, and Applications
    (TOMCCAP)*, so a blacklist entry typed without the trailing abbreviation
    missed it — and matched only when the record happened to carry an alternate
    name without the suffix.  Strip that alternate and the same entry, the same
    papers and the same run stopped flagging them.

    Padded with spaces on both sides so the phrase matches on word boundaries:
    blacklisting "Sensors" catches a venue named "Sensors" and one named "IEEE
    Sensors Journal", but never "Biosensors".  That it can be wide is the point
    of the label saying which identifier it matched on, and of the ⚠ the venue
    box puts on the identifiers that reach furthest.
    """
    for issn in issns:
        if issn in user_index.by_issn:
            return _best(user_index.by_issn[issn]), "issn", "on_list"
    for dom in ([canonical_domain] if canonical_domain else []) + list(all_domains):
        entry, matched_on = user_index.domain_match(dom)
        if entry:
            return entry, matched_on, "on_list"
    for nm in norm_names:
        if nm in user_index.by_name:
            entry = _best(user_index.by_name[nm])
            # The name is exactly the listed one — but if this paper carries an
            # ISSN of its own and it is not the listed one, two readings survive
            # and the run cannot choose between them: the same journal under its
            # other registered number (print and online hold one each, and a
            # directory may have handed over only one), or a different journal
            # that happens to share the title exactly.  Both are ordinary, so
            # this is what `review` is for — a person can settle it in a moment
            # and the run must not assert it either way.
            if entry.get("issns") and issns:
                return entry, "name_exact_issn_differs", "review"
            return entry, "name_exact", "on_list"
    for nm in norm_names:
        padded = f" {nm} "
        for entry in user_index.entries:
            # Only entries whose name is their widest claim — see
            # user_entry_signals.  An entry carrying an ISSN has had its say
            # above, by ISSN and by exact name; its title is not also a phrase.
            if "name_phrase" not in user_entry_signals(entry):
                continue
            listed = entry.get("normalized_name")
            if listed and f" {listed} " in padded:
                return entry, "name_phrase", "on_list"
    return None


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

    # ── the user's own blacklist, asked first ────────────────────────────
    # Everything below this point is the tool's judgement about a venue.  This
    # is not: a blacklist entry is an instruction from the person running the
    # check — *treat this venue as predatory for this run* — and an instruction
    # that the tool's own judgement can overrule is not an instruction.
    #
    # The order is the whole point.  Blacklist entries live in the same index as
    # Beall's and match there identically, but the preprint rule below returns
    # `no_venue` before any matching happens, so blacklisting a preprint server
    # did nothing at all: arxiv.org in the blacklist, papers from arXiv still
    # `no_venue`, and no message anywhere to say the entry had been ignored.
    # Asked here, the user's list is answered before the tool decides there is
    # nothing to answer about.
    #
    # Exact signals only — the listed host or a host under it, an exact ISSN, an
    # exact name — because those are the three ways the box that built the entry
    # says it will match, and a fuzzy hit on a name someone typed themselves
    # would flag venues they did not name.  The name match is NOT put through
    # the corroboration downgrade the Beall path applies: that rule exists
    # because a third-party list's name may coincide with a different journal,
    # and it has no business overruling a name this user chose deliberately.
    if index.user_index is not None:
        hit = _user_blacklist_hit(index.user_index, canonical_domain, all_domains,
                                  norm_names, issns)
        if hit:
            entry, matched_on, status = hit
            return _hit(r, entry, status, matched_on)

    # ── no-venue / preprint classification (venue signals only) ──────────
    is_preprint = any(d in PREPRINT_DOMAINS for d in all_domains) or (
        not all_domains and any(n in PREPRINT_NAMES for n in norm_names)
    )
    # Both outcomes are 'no_venue' — there is no journal to compare against a
    # list — but they are not the same finding, and the report has to say which.
    # A preprint HAS a venue and it is one no list can cover; a record with no
    # venue fields at all could not be checked. Recorded in ``matched_on``, which
    # is where a non-match already carries its explanation (see the 'error' row).
    if is_preprint:
        r["status"] = "no_venue"
        r["matched_on"] = "preprint"
        return r
    if not all_domains and not norm_names and not issns:
        r["status"] = "no_venue"
        r["matched_on"] = "no_venue_metadata"
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
        # Joined in the same order, so the first name here and the first list
        # above both describe the entry `matched_url` points at.
        r["provider"] = "; ".join(dict.fromkeys(e.get("provider", "") for e in alt_hits
                                                if e.get("provider")))
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
    r["provider"] = entry.get("provider", "")
    # Kept so a later stage can confirm against the entry that ACTUALLY matched
    # rather than looking one up again by name.  resolve._resolve_name_exact used
    # to re-derive it from the shared name index, which picks a different entry
    # as soon as two catalogues list the same journal title.
    r["matched_entry"] = entry
    r["list_source"] = entry["list_source"]
    r["matched_name"] = entry["name"]
    r["matched_url"] = entry["url"]
    r["matched_on"] = matched_on
    r["authentic_url"] = entry.get("authentic_url", "")
    r["authentic_domain"] = entry.get("authentic_domain", "")
    return r
