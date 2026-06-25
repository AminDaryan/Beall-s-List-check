"""Resolve 'review' verdicts using the paper's DOI (via Crossref).

``match.py`` makes the first-pass verdict from Semantic Scholar metadata alone.
Some of its ``review`` results are *soft/contested* — the venue name matches a
listed journal but the website differs, an alternate URL points at a listed
site, a free PDF sits on a listed site, or a hijacked-journal name matched.  In
each case the paper's **DOI** is the authoritative tiebreaker: Crossref (the
publisher-deposited registry) records which journal that exact paper was really
published in.

:func:`resolve_review` runs four resolvers over one result; each is a no-op
unless the row is the specific kind of ``review`` it handles:

  * :func:`_clear_hijacked_if_genuine` — a hijacked-by-name review.
  * :func:`_resolve_alternate_url`     — an alternate-URL review.
  * :func:`_resolve_open_access_pdf`   — a free-PDF-on-a-listed-site review.
  * :func:`_resolve_name_exact`        — a name-matches-but-website-differs review.

The ONE rule they all obey: **only act on positive proof.**  A row is promoted to
``on_list`` only when the DOI confirms the listed venue, and cleared to ``clean``
only when the DOI positively identifies the paper's own, unlisted venue (by
domain/ISSN — never by a shared name).  Anything ambiguous stays ``review`` —
a false clear is the one error this check must not make.

Every resolver makes a (cached) Crossref lookup, so ``classify_corpus`` calls
:func:`resolve_review` only when ``--crossref`` is enabled.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import normalize_host, normalize_name, normalize_issn
from match import _hit, _review_reason, _best
import data_quality                       # the shared, cached Crossref client


def resolve_review(r, paper, index):
    """Try to settle one ``review`` verdict using the paper's DOI.

    Runs the four DOI resolvers in order (predatory-confirming ones first); each
    no-ops unless ``r`` is the kind of review it handles.  Mutates ``r`` in place.
    Called once per paper by ``classify_corpus``, but ONLY when Crossref is
    enabled — these all hit the network.
    """
    _clear_hijacked_if_genuine(r, paper, index)
    _resolve_alternate_url(r, paper, index)
    _resolve_open_access_pdf(r, paper, index)
    _resolve_name_exact(r, paper, index)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _domains_of(paper):
    """All venue hosts the record gives — canonical publicationVenue.url + alternates."""
    pv = paper.get("publicationVenue") or {}
    hosts = [normalize_host(pv["url"])] if pv.get("url") else []
    hosts += [normalize_host(u) for u in (pv.get("alternate_urls") or []) if u]
    return [h for h in hosts if h]


def _same_host(a, b):
    """True if two hosts are equal or one is a subdomain of the other."""
    return bool(a) and bool(b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def _crossref_publisher_domain(msg):
    """The real publisher's host from a Crossref record — its landing-page URL."""
    url = (((msg.get("resource") or {}).get("primary") or {}).get("URL") or "")
    return normalize_host(url)


def _crossref_matches(msg, target, real_domain, *, allow_name=True):
    """How a paper's Crossref-registered venue matches *target*, or ``None``.

    ``target`` is a venue descriptor — ``{"domain", "issns", "normalized_name"}``
    — and may be either a listed Beall entry (to CONFIRM a predatory match) or the
    paper's own canonical venue (to CLEAR a false alarm).  The DOI's registration
    is the authoritative identity of the paper's venue; we accept a match on any
    unambiguous field — ISSN, publisher website, or exact journal name — and name
    which one for the audit trail.  Empty target fields never match.

    ``allow_name=False`` disables the journal-name signal.  Use it whenever the
    NAME is the very thing in doubt (a name_exact review, where the paper and the
    listed entry already share a name): there, a name match proves nothing and
    would mislabel a legitimate same-named journal — only domain/ISSN discriminate.
    """
    cr_issns = {normalize_issn(i) for i in (msg.get("ISSN") or []) if normalize_issn(i)}
    tgt_issns = {normalize_issn(i) for i in (target.get("issns") or []) if normalize_issn(i)}
    if cr_issns and tgt_issns and (cr_issns & tgt_issns):
        return "ISSN"
    if real_domain and target.get("domain") and _same_host(real_domain, target["domain"]):
        return "publisher website"
    if allow_name:
        cr_title = ((msg.get("container-title") or [""])[0] or "")
        if cr_title and target.get("normalized_name") and \
                normalize_name(cr_title) == target["normalized_name"]:
            return "journal name"
    return None


def _canonical_venue_descriptor(paper):
    """A descriptor (domain / issns / normalized_name) for the paper's OWN
    canonical venue, shaped like a snapshot entry so :func:`_crossref_matches`
    can test it.

    Used to clear a review ONLY on positive evidence that the DOI belongs to this
    paper's canonical venue — never on the weaker "the real publisher merely isn't
    listed" signal, which a predatory publisher recorded under a different domain
    would slip through.
    """
    pv = paper.get("publicationVenue") or {}
    name = pv.get("name") or paper.get("venue") or ""
    return {
        "domain": normalize_host(pv["url"]) if pv.get("url") else "",
        "issns": [pv["issn"]] if pv.get("issn") else [],
        "normalized_name": normalize_name(name),
    }


# ---------------------------------------------------------------------------
# (1) Hijacked-by-name review  ->  clear to clean if it's the genuine journal
# ---------------------------------------------------------------------------

def _clear_hijacked_if_genuine(r, paper, index):
    """Clear a hijacked-BY-NAME 'review' to 'clean' when the paper is provably not
    the predatory clone.

    A hijacked listing flags a counterfeit that stole a real journal's NAME, so a
    name match is ambiguous.  We clear the paper only on POSITIVE evidence that it
    is the genuine journal — that its venue is the authentic journal's OWN site:
      (A) its Semantic Scholar venue website is the authentic journal's site (from
          Beall's 'authentic journal' column); or
      (B) its DOI resolves (via Crossref) to that same authentic site.
    We deliberately do NOT clear on the weaker "the DOI's publisher is neither the
    counterfeit nor itself listed" test: a clone that deposited DOIs under a domain
    Beall never recorded would pass it, and a legitimate but DIFFERENT same-named
    journal (e.g. a third 'Phyton') would clear without proof it is the one meant.
    Anything short of a match to the authentic site stays 'review' for a human.
    Cleared papers become 'clean' but keep a plain explanation (in the Match
    column and on the Data-quality sheet) of the doubt and how it was resolved.
    """
    if not (r["status"] == "review" and r["list_source"] == "hijacked"
            and (r.get("matched_on") or "").startswith("name")):
        return
    counterfeit = normalize_host(r.get("matched_url") or "")
    paper_domains = _domains_of(paper)

    # (A) Paper hosted at the authentic journal's own site.
    authentic = r.get("authentic_domain") or ""
    if authentic and any(_same_host(d, authentic) for d in paper_domains):
        _mark_cleared(r, counterfeit,
                      f"this paper's venue is the authentic journal's own website "
                      f"({authentic})")
        return

    # (B) DOI resolves to the AUTHENTIC journal's OWN website -> it is the genuine
    #     journal, not the clone. Positive identification only: without an authentic
    #     site to match, or if the DOI resolves anywhere else, we leave it in review.
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").strip()
    if not (authentic and doi):
        return                                          # no authentic site to match / no DOI -> stay review
    msg = data_quality._crossref_lookup(doi)
    if not msg:
        return
    real_domain = _crossref_publisher_domain(msg)
    if not (real_domain and _same_host(real_domain, authentic)):
        return                                          # not positively the authentic site -> stay review
    if index.domain_match(real_domain)[0] is not None:
        return                                          # defensive: never clear to a listed host
    publisher = (msg.get("publisher") or "").strip()
    journal = ((msg.get("container-title") or [""])[0] or "").strip()
    who = publisher or journal or real_domain
    detail = journal or publisher or real_domain
    _mark_cleared(r, counterfeit,
                  f"this paper's DOI shows it was published at the authentic journal's "
                  f"own website ({real_domain}; {who}, {detail}), not the counterfeit")


def _mark_cleared(r, counterfeit, why):
    """Flip a cleared hijacked paper to 'clean' and record the transparent reason."""
    expl = (f"Cleared after a check. The venue NAME matched a hijacked-journal entry "
            f"on Beall's List (the counterfeit at {counterfeit or '—'}), which raised "
            f"doubt — but {why}, so this paper is not in the predatory clone. ('Clean' "
            "here means 'not on Beall's List', not 'proven reputable'.)")
    r["status"] = "clean"
    r["cleared_reason"] = expl
    r.setdefault("data_quality", []).append(expl)   # keep it visible on the Data-quality sheet


# ---------------------------------------------------------------------------
# (2) Alternate-URL review  ->  confirm to on_list, or clear to clean
# ---------------------------------------------------------------------------

def _alt_listed_entries(paper, index):
    """The listed Beall entries reached via this paper's ALTERNATE venue URLs.

    Mirrors the alternate-URL tier in match._classify (same hosts, same
    dedupe-by-name) so the resolver below reasons about exactly the entries that
    produced the ``domain_alternate_url`` flag.
    """
    pv = paper.get("publicationVenue") or {}
    entries, seen = [], set()
    for u in (pv.get("alternate_urls") or []):
        dom = normalize_host(u) if u else ""
        if not dom:
            continue
        entry, _ = index.domain_match(dom)
        if entry and entry["name"] not in seen:
            seen.add(entry["name"])
            entries.append(entry)
    return entries


def _resolve_alternate_url(r, paper, index):
    """Resolve an alternate-URL 'review' using the paper's DOI (Crossref).

    A ``domain_alternate_url`` review means a NON-canonical venue URL pointed at
    a listed site — but Semantic Scholar sometimes merges two same-named journals
    into one record, so that alternate may belong to a different journal than
    this paper's.  The paper's DOI is the authoritative tiebreaker: Crossref names
    the journal this specific paper was really published in.  Three outcomes:

      * Crossref's venue IS one of the listed alternates  -> promote to on_list
        (the alternate really is this paper's venue).
      * Crossref POSITIVELY identifies the paper's own canonical venue (by ISSN,
        website, or exact name) and that venue is not listed -> the alternate was
        merge noise; clear to clean.
      * anything else — no DOI, no Crossref record, or a result that does NOT
        positively identify the canonical venue (including a real publisher whose
        domain merely isn't on the list) -> leave it as review.

    The clear branch deliberately demands POSITIVE evidence rather than the weaker
    "real publisher isn't listed": a predatory publisher Beall recorded under a
    different domain would otherwise be wrongly cleared.  When in doubt we keep the
    paper in review — a false clear is the one error this check must not make.

    Makes one (cached) Crossref lookup, exactly like the hijacked clearer above.
    """
    if not (r["status"] == "review" and r.get("matched_on") == "domain_alternate_url"):
        return
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").strip()
    if not doi:
        return                                          # coverage gap: no DOI -> stay review
    msg = data_quality._crossref_lookup(doi)
    if not msg:
        return                                          # not in Crossref / lookup failed -> stay review
    real_domain = _crossref_publisher_domain(msg)

    # (1) Confirm predatory: the DOI's registered venue IS one of the flagged
    #     listed alternates.  Predatory wins ties — confirm runs before clear.
    for entry in _alt_listed_entries(paper, index):
        how = _crossref_matches(msg, entry, real_domain)
        if how:
            _confirm_alternate(r, entry, how, doi)
            return

    # (2) Clear — ONLY on positive evidence the DOI belongs to this paper's own
    #     canonical venue, AND that venue is on no Beall list.  We do NOT clear on
    #     "the real publisher isn't listed" alone; anything short of a positive
    #     canonical match stays 'review' for a human.
    how = _crossref_matches(msg, _canonical_venue_descriptor(paper), real_domain)
    if how and real_domain and index.domain_match(real_domain)[0] is None:
        publisher = (msg.get("publisher") or "").strip()
        journal = ((msg.get("container-title") or [""])[0] or "").strip()
        who = publisher or journal or real_domain
        detail = journal or publisher or real_domain
        _mark_alt_cleared(r, who, detail, real_domain, how)
    # else: ambiguous -> leave as review


def _confirm_alternate(r, entry, how, doi):
    """Promote an alternate-URL review to on_list — the DOI proves the paper's
    venue IS the listed entry.

    Routes through match._hit so the standard downgrades still apply: a weak-list
    entry (vanity press / fake-metrics) stays 'review', and the verdict is
    re-derived for that case.  A transparent confirmation note is recorded.
    """
    _hit(r, entry, "on_list", "domain_alternate_url_doi")
    r["review_reason"] = _review_reason(r)   # "" once on_list; real text if a weak-list downgrade kept it review
    note = (f"Confirmed via DOI. Semantic Scholar listed an alternate web link for "
            f"this venue pointing to the listed '{entry['name']}', and the paper's "
            f"DOI ({doi}) registers it to that same venue (matched by {how}) — so "
            f"this paper really is in the listed venue, not a different same-named "
            f"journal.")
    r.setdefault("data_quality", []).append(note)


def _mark_alt_cleared(r, who, detail, real_domain, how):
    """Flip an alternate-URL review to 'clean': the DOI POSITIVELY identifies the
    paper's own (unlisted) canonical venue, so the listed alternate was Semantic
    Scholar merge noise belonging to a different same-named journal."""
    listed = r.get("matched_name") or "a listed site"
    expl = (f"Cleared after a check. Semantic Scholar listed an alternate web link "
            f"for this venue pointing to the listed '{listed}', which raised doubt — "
            f"but the paper's DOI identifies its venue as {who} ({detail}; "
            f"{real_domain}), matched by {how}: this is the paper's own canonical "
            f"venue and is not on Beall's List. Semantic Scholar had merged two "
            f"same-named journals; this paper belongs to the unlisted one. ('Clean' "
            f"here means 'not on Beall's List', not 'proven reputable'.)")
    r["status"] = "clean"
    r["cleared_reason"] = expl
    r["review_reason"] = ""
    r.setdefault("data_quality", []).append(expl)   # keep it visible on the Data-quality sheet


# ---------------------------------------------------------------------------
# (3) Open-access-PDF review  ->  confirm to on_list, or clear to clean
# ---------------------------------------------------------------------------

def _resolve_open_access_pdf(r, paper, index):
    """Resolve an open-access-PDF 'review' using the paper's DOI (Crossref).

    A ``domain_open_access_pdf`` review means the venue itself was NOT on the list,
    but the paper's free PDF is hosted on a listed site — and a PDF can be
    re-hosted by someone other than the publisher, so it is only a soft signal.
    The DOI is the authoritative tiebreaker: Crossref records who actually
    published the paper.  Three outcomes:

      * Crossref says the listed PDF host IS this paper's publisher -> on_list
        (it really was published there, not merely re-hosted).
      * Crossref POSITIVELY identifies the paper's own canonical venue and that
        venue is not listed -> the PDF was a re-host; clear to clean.
      * anything else — no DOI, no record, or no positive identification -> review.

    Same positive-proof rule as the alternate-URL resolver: never clear on the
    weak "the real publisher isn't listed" signal alone.  One (cached) lookup.
    """
    if not (r["status"] == "review" and r.get("matched_on") == "domain_open_access_pdf"):
        return
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").strip()
    if not doi:
        return                                          # coverage gap: no DOI -> stay review
    msg = data_quality._crossref_lookup(doi)
    if not msg:
        return                                          # not in Crossref / lookup failed -> stay review
    real_domain = _crossref_publisher_domain(msg)

    # (1) Confirm: the DOI's publisher IS the listed site the free PDF sits on ->
    #     the paper was genuinely published there, not merely re-hosted.
    oa = paper.get("openAccessPdf") or {}
    oa_domain = normalize_host(oa.get("url")) if oa.get("url") else ""
    entry = index.domain_match(oa_domain)[0] if oa_domain else None
    if entry:
        how = _crossref_matches(msg, entry, real_domain)
        if how:
            _hit(r, entry, "on_list", "domain_open_access_pdf_doi")
            r["review_reason"] = _review_reason(r)   # "" once on_list; real text if a weak-list downgrade kept it review
            r.setdefault("data_quality", []).append(
                f"Confirmed via DOI. The paper's free PDF is hosted on the listed "
                f"'{entry['name']}', and the paper's DOI ({doi}) registers it as "
                f"published there too (matched by {how}) — so it was genuinely "
                f"published by the listed venue, not merely re-hosted on it.")
            return

    # (2) Clear — ONLY on positive evidence the DOI belongs to this paper's own
    #     canonical venue, AND that venue is on no Beall list.  Anything short of a
    #     positive canonical match stays 'review' for a human.
    how = _crossref_matches(msg, _canonical_venue_descriptor(paper), real_domain)
    if how and real_domain and index.domain_match(real_domain)[0] is None:
        publisher = (msg.get("publisher") or "").strip()
        journal = ((msg.get("container-title") or [""])[0] or "").strip()
        who = publisher or journal or real_domain
        detail = journal or publisher or real_domain
        _mark_oa_cleared(r, who, detail, real_domain, how)
    # else: ambiguous -> leave as review


def _mark_oa_cleared(r, who, detail, real_domain, how):
    """Flip an open-access-PDF review to 'clean': the DOI POSITIVELY identifies the
    paper's own (unlisted) canonical venue, so the listed site merely RE-HOSTED the
    free PDF and did not publish the paper."""
    listed = r.get("matched_name") or "a listed site"
    expl = (f"Cleared after a check. The paper's free PDF is hosted on the listed "
            f"'{listed}', which raised doubt — but the paper's DOI identifies its "
            f"venue as {who} ({detail}; {real_domain}), matched by {how}: this is the "
            f"paper's own canonical venue and is not on Beall's List, so the listed "
            f"site merely re-hosted the PDF and did not publish the paper. ('Clean' "
            f"here means 'not on Beall's List', not 'proven reputable'.)")
    r["status"] = "clean"
    r["cleared_reason"] = expl
    r["review_reason"] = ""
    r.setdefault("data_quality", []).append(expl)   # keep it visible on the Data-quality sheet


# ---------------------------------------------------------------------------
# (4) name_exact review  ->  confirm to on_list, or clear to clean
# ---------------------------------------------------------------------------

def _resolve_name_exact(r, paper, index):
    """Resolve a name_exact 'review' using the paper's DOI (Crossref).

    A ``name_exact`` review means the venue NAME matches a listed journal, but the
    paper's own website is NOT the listed one — so it may be a different journal
    that merely shares the name.  The DOI settles which journal this paper is in:

      * Crossref's registered venue IS the listed journal (by DOMAIN or ISSN) ->
        the paper really is in it -> on_list.
      * Crossref POSITIVELY identifies the paper's own venue (by DOMAIN or ISSN),
        and that venue is not listed -> a different same-named journal -> clean.
      * anything else -> stays review.

    Crucially this matches on DOMAIN/ISSN only, NEVER on name: the name is the very
    thing in doubt (both sides share it), so a name match would prove nothing and
    could mislabel the legitimate journal.  Same positive-proof rule as the others.
    """
    if not (r["status"] == "review" and r.get("matched_on") == "name_exact"):
        return
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").strip()
    if not doi:
        return                                          # coverage gap: no DOI -> stay review
    msg = data_quality._crossref_lookup(doi)
    if not msg:
        return                                          # not in Crossref / lookup failed -> stay review
    real_domain = _crossref_publisher_domain(msg)

    # (1) Confirm: the DOI's registered venue IS the listed journal (domain/ISSN only).
    nm = normalize_name(r.get("matched_name") or "")
    entry = _best(index.by_name[nm]) if nm in index.by_name else None
    if entry:
        how = _crossref_matches(msg, entry, real_domain, allow_name=False)
        if how:
            _hit(r, entry, "on_list", "name_exact_doi")
            r["review_reason"] = _review_reason(r)
            r.setdefault("data_quality", []).append(
                f"Confirmed via DOI. The venue name matches the listed '{entry['name']}', "
                f"and the paper's DOI ({doi}) registers it to that same journal (matched "
                f"by {how}, not the shared name) — so this paper really is in the listed "
                f"journal, not a different same-named one.")
            return

    # (2) Clear — ONLY on positive DOMAIN/ISSN evidence the DOI belongs to this
    #     paper's own venue, AND that venue is on no Beall list.
    how = _crossref_matches(msg, _canonical_venue_descriptor(paper), real_domain,
                            allow_name=False)
    if how and real_domain and index.domain_match(real_domain)[0] is None:
        publisher = (msg.get("publisher") or "").strip()
        journal = ((msg.get("container-title") or [""])[0] or "").strip()
        who = publisher or journal or real_domain
        detail = journal or publisher or real_domain
        _mark_name_exact_cleared(r, who, detail, real_domain, how)
    # else: ambiguous -> leave as review


def _mark_name_exact_cleared(r, who, detail, real_domain, how):
    """Flip a name_exact review to 'clean': the DOI POSITIVELY identifies (by
    domain/ISSN) the paper's own venue as a DIFFERENT, unlisted journal that
    merely shares the listed journal's name."""
    listed = r.get("matched_name") or "a listed journal"
    expl = (f"Cleared after a check. The venue NAME matched the listed '{listed}', "
            f"which raised doubt — but the paper's DOI identifies its venue as {who} "
            f"({detail}; {real_domain}), matched by {how} (not the shared name): this "
            f"is a different journal that merely shares the name, and it is not on "
            f"Beall's List. ('Clean' here means 'not on Beall's List', not 'proven "
            f"reputable'.)")
    r["status"] = "clean"
    r["cleared_reason"] = expl
    r["review_reason"] = ""
    r.setdefault("data_quality", []).append(expl)   # keep it visible on the Data-quality sheet
