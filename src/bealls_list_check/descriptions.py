"""Plain-English text shown in the workbook — the "what we say" layer.

Everything a reader sees *in words* lives here, kept separate from the openpyxl
rendering in ``report.py`` so the wording can be edited without touching any
layout code.  Nothing in this module does I/O; the functions take a result dict
(``r``) and return strings.

  * SIGNAL_INFO   — for each match signal (``matched_on``): a short label, the
                    verdict it leads to, and a thorough plain-language
                    explanation.  The single source of truth, reused by both the
                    Match column and the Legend so the two never drift.
  * LIST_INFO     — plain name + weight for each list a match can come from:
                    Beall's five, and the blacklist supplied for the run.
  * _on_list_why  — the one-line "Why flagged" sentence for an on_list match.
  * _LEGEND_SECTIONS — the entire Legend sheet's content, as data.

To change wording shown to users, edit here.  To change how it is laid out in
Excel, edit report.py.
"""

# Plain-language description of each match signal, used both in the Match column
# and to build the Legend so the two never drift.  Tuple = (short label shown in
# the cell, which verdict it leads to, thorough explanation with a FIXED real
# example).  The examples are constant real Beall's List cases — they do NOT
# depend on the corpus, so every generated workbook shows the same illustrations.
SIGNAL_INFO = {
    "domain": (
        "the journal's own website is on the list",
        "on_list",
        "The journal's website address is itself on Beall's List. Example: a "
        "paper in a journal hosted at www.scirp.org is matched because "
        "scirp.org (Scientific Research Publishing) is a publisher on the list "
        "— the journal's own home is a listed site. This is the most reliable "
        "signal.",
    ),
    "domain_subdomain": (
        "the journal's website is part of a listed site",
        "on_list",
        "The journal's website is a section of a bigger website that is on the "
        "list. Example: a journal at ojs.bilpublishing.com is matched because "
        "it sits under bilpublishing.com (Bilingual Publishing Co.), a listed "
        "publisher.",
    ),
    "issn": (
        "the journal's ISSN is on the list",
        "on_list",
        "An ISSN is the unique ID number every journal has. Here the journal's "
        "ISSN equals the ISSN of a journal on the list. (Rarely used, because "
        "Beall's List mostly records names and websites, not ISSNs.)",
    ),
    "name_exact": (
        "the journal/publisher name exactly matches the list",
        "on_list",
        "The journal or publisher name is exactly the same as a name on the "
        "list (capitalisation, accents and punctuation are ignored). Example: a "
        "venue named 'OMICS Publishing Group' matches the listed 'OMICS "
        "Publishing Group'. Asserted as on_list only when the paper's OWN venue "
        "website is the listed site too; if the name matches but the website is "
        "different (many journals share a name), it is downgraded to review.",
    ),
    "name_phrase": (
        "your blacklisted name appears in the venue's name",
        "on_list",
        "Only from a blacklist supplied for the run: the name you listed appears "
        "as a whole phrase inside the venue's own name. Example: blacklisting "
        "'ACM Transactions on Multimedia Computing, Communications, and "
        "Applications' matches a venue Semantic Scholar records under that name "
        "followed by its abbreviation, '(TOMCCAP)'. Word boundaries are "
        "respected, so 'Sensors' matches 'IEEE Sensors Journal' but never "
        "'Biosensors' — a short name can therefore reach further than intended.",
    ),
    "name_exact_issn_differs": (
        "your blacklisted name matches, but the ISSN does not",
        "review",
        "Only from a blacklist supplied for the run: the venue's name is exactly "
        "the one you listed, and the paper also carries an ISSN that is not the "
        "one your entry holds. Two readings survive. It may be the same journal "
        "under its other registered number — a journal holds one ISSN for print "
        "and another for online, and a directory may have handed over only one. "
        "Or it may be a different journal that happens to share the title "
        "exactly. Both are ordinary, so the run refuses to choose: a person can "
        "settle it by opening the venue, and the row is flagged for review "
        "rather than asserted.",
    ),
    "name_fuzzy": (
        "the journal name is very similar to a listed name",
        "review",
        "The name is almost — but not exactly — the same as a listed name (at "
        "least 93% similar): a spelling variant or typo, e.g. 'Internatonal "
        "Journal of Science' vs the listed 'International Journal of Science'. "
        "Because it is not exact it is only ever flagged for review, never as a "
        "definite hit.",
    ),
    "domain_alternate_url": (
        "an alternate web link points to a listed site",
        "review",
        "The venue's main website looks fine, but one of the OTHER web links "
        "Semantic Scholar lists for it points to a listed site (e.g. a "
        "scirp.org link). Semantic Scholar sometimes merges two journals that "
        "share a name, so that link may belong to a different journal — a human "
        "should check.",
    ),
    "name_exact_doi": (
        "the name matched and the DOI confirms the journal",
        "on_list",
        "The venue name matched a listed journal, and the paper's DOI (checked "
        "against Crossref, the publisher registry) shows — by publisher website or "
        "ISSN, not the shared name — that the paper really is in that listed "
        "journal. This resolves the 'many journals share a name' doubt, so it is "
        "asserted as on_list. Example: a venue named like a listed journal whose "
        "DOI registers it to that journal's own ISSN.",
    ),
    "domain_alternate_url_doi": (
        "an alternate link matched and the DOI confirms it",
        "on_list",
        "An alternate web link for the venue pointed to a listed site, and the "
        "paper's DOI (checked against Crossref, the publisher registry) shows it "
        "was actually published there. The DOI resolves the 'merged journals' "
        "doubt that would otherwise keep this as review, so it is asserted as "
        "on_list. Example: a venue whose main site is fine but whose alternate "
        "link is scirp.org, where the DOI registers the paper to SCIRP.",
    ),
    "domain_open_access_pdf": (
        "the PDF is hosted on a listed site",
        "review",
        "The journal itself could not be identified, but the free PDF of the "
        "paper is hosted on a listed site (e.g. scirp.org). Suggestive but not "
        "conclusive, so it is flagged for review.",
    ),
    "domain_open_access_pdf_doi": (
        "the PDF is on a listed site and the DOI confirms it",
        "on_list",
        "The paper's free PDF sits on a listed site, and the paper's DOI (checked "
        "against Crossref, the publisher registry) shows it was actually published "
        "there — not merely re-hosted. The DOI resolves the re-hosting doubt, so "
        "this is asserted as on_list. Example: a paper whose only listed signal is "
        "a frontiersin.org PDF, where the DOI registers the paper to Frontiers.",
    ),
}

# Plain-language name + weight for each list a match can come from.
#
# The last one is not Beall's.  It is the blacklist supplied for this run, and it
# used to be missing here — so a row matched from it reported its source as the
# bare word "blacklist", which reads like a category of Beall's List rather than
# like something the reader typed in themselves half an hour earlier.
LIST_INFO = {
    "publishers":         ("predatory publisher", "core list"),
    "standalone_journal": ("standalone predatory journal", "core list"),
    "hijacked":           ("hijacked / cloned journal", "core list"),
    "vanity_press":       ("vanity press (book publisher)", "weak — review only"),
    "misleading_metric":  ("fake-metrics company", "weak — review only"),
    "blacklist":          ("your own blacklist (not Beall's List)",
                           "supplied for this run"),
}


def _signal_plain(matched_on):
    """Short plain-language phrase for a match signal (falls back to the code)."""
    info = SIGNAL_INFO.get(matched_on)
    return info[0] if info else (matched_on or "—")


def _list_plain(list_source):
    """Plain-language name(s) for a (possibly '; '-joined) list_source."""
    parts = [p.strip() for p in (list_source or "").split(";") if p.strip()]
    if not parts:
        return "—"
    return "; ".join(LIST_INFO.get(p, (p,))[0] for p in parts)


# Short note shown on the first Match row for statuses that matched nothing.
#
# 'no_venue' covers two different findings and used to state them as one
# undecided sentence ("this is a preprint, OR the record has no venue
# information"), which left the reader to work out which had happened. They are
# split by ``matched_on`` below: a preprint is a venue no list can cover, while a
# record with no venue fields is one the check could not examine at all. The
# distinction matters when the question is "why is this paper not in the
# legitimate set?".
_NONMATCH_NOTES = {
    "clean":        "The venue was identified and is not on Beall's List.",
    "no_venue":     "No journal venue to classify — this is a preprint, or the "
                    "record has no venue information.",
}

_NO_VENUE_NOTES = {
    "preprint": (
        "Not reviewed against any list: this is a preprint (e.g. arXiv), which "
        "has no journal or publisher, so it cannot appear on a predatory-venue "
        "list. Nothing here says the paper is either sound or unsound."
    ),
    "no_venue_metadata": (
        "Could not be reviewed: the record carries no venue information at all — "
        "no journal name, no website and no ISSN — so there was nothing to "
        "compare against any list. This is a gap in the metadata, not a finding "
        "about the paper. Supply the venue (or the DOI) to have it checked."
    ),
}


def _nonmatch_note(r):
    """The Match-column note for a status that matched nothing.

    Split out of ``_NONMATCH_NOTES`` so that ``no_venue`` can say which of its
    two causes applied, without giving either one its own status — the batch
    CSV's documented five status values are a published contract.
    """
    status = r.get("status")
    if status == "no_venue":
        return _NO_VENUE_NOTES.get(r.get("matched_on") or "",
                                   _NONMATCH_NOTES["no_venue"])
    return _NONMATCH_NOTES.get(status, "")


def _on_list_why(r):
    """Detailed plain-English 'why' for a high-confidence (on_list) match.

    Names BOTH the paper's matching signal and the concrete listed entry it hit,
    so the reader sees exactly what matched — e.g. an exact-name match on a
    generic title is now visible as such and can be sanity-checked against the
    Data-quality sheet's "merged venue" warning.
    """
    mo = r.get("matched_on")
    listed = r.get("matched_name") or "a listed entry"
    # Said before anything else, and said as the reader's own doing: this row is
    # not a finding about Beall's List, it is the blacklist supplied for this run
    # being applied.  Naming the signal as well, because "you asked for this" is
    # only half an answer — which of the entry's identifiers matched decides how
    # widely it will have caught, and a name is the wide one.
    if (r.get("list_source") or "") == "blacklist":
        how = {
            "domain": f"its website ({r.get('venue_domain') or '—'}) is the site you listed",
            "domain_subdomain": (f"its website ({r.get('venue_domain') or '—'}) sits under "
                                 "the site you listed"),
            "issn": f"its ISSN ({r.get('issn') or '—'}) is the one you listed",
            "name_exact": f"its name matches '{listed}', which you listed",
            "name_phrase": (f"'{listed}' — which you listed — appears as a whole phrase "
                            f"inside its name, '{r.get('venue') or '—'}'"),
        }.get(mo, f"it matches '{listed}', which you listed")
        return (f"you put this venue on the blacklist for this run, and {how}. "
                "This is your own entry, not a Beall's List finding — the run "
                "treats it as predatory because you said to.")
    if mo == "domain":
        return (f"the venue's own website ({r.get('venue_domain') or '—'}) is on "
                f"Beall's List as '{listed}' — the most reliable signal.")
    if mo == "domain_subdomain":
        return (f"the venue's website ({r.get('venue_domain') or '—'}) sits under "
                f"the listed site of '{listed}' — a high-confidence match.")
    if mo == "issn":
        return (f"the venue's ISSN ({r.get('issn') or '—'}) matches the listed "
                f"'{listed}' — a high-confidence match.")
    if mo == "name_exact":
        return (f"the venue name '{r.get('venue') or '—'}' exactly matches the "
                f"listed '{listed}' — a high-confidence match.")
    if mo == "domain_alternate_url_doi":
        return (f"an alternate web link for this venue pointed to the listed "
                f"'{listed}', and the paper's DOI confirms (via Crossref, the "
                f"publisher registry) it was published there — so the alternate is "
                f"this paper's real venue, not a different same-named journal.")
    if mo == "domain_open_access_pdf_doi":
        return (f"the paper's free PDF is hosted on the listed '{listed}', and the "
                f"paper's DOI confirms (via Crossref, the publisher registry) it was "
                f"published there — not merely re-hosted, so this is a real match.")
    if mo == "name_exact_doi":
        return (f"the venue name matches the listed '{listed}', and the paper's DOI "
                f"confirms (via Crossref, by publisher website or ISSN — not the shared "
                f"name) it really is that journal, not a different same-named one.")
    return (f"{_signal_plain(mo)} ('{listed}') — so it appears on Beall's List "
            "(high-confidence match).")


# Legend-sheet content as one declarative structure.  Each section is a titled
# mini-table of (col-A, col-B, col-C) rows.  headers=None renders the rows as
# full-width bullet lines (the caveats); swatch=True tints column A by status
# to match the Results sheet colors.  Add/edit documentation here only — the
# renderer in report.py is generic.
_LEGEND_TITLE = "Beall's List Check — Legend / Data Dictionary"
_LEGEND_SECTIONS = [
    {
        "title": "What this report is — input → output",
        "headers": None,
        "rows": [
            ("WHAT IT DOES: each paper's publication venue (its journal/publisher) "
             "is compared against Beall's List of allegedly predatory venues, and "
             "given a STATUS (see the next section).",),
            ("INPUT: one Semantic Scholar (\"S2\") record per paper, giving the "
             "venue's name, website, ISSN and DOI. The check classifies each "
             "paper from this metadata as it is; when a record's metadata looks "
             "incomplete or self-contradictory, that paper is also listed on the "
             "'Data quality' sheet so you know to double-check it before trusting "
             "its result.",),
            ("OUTPUT (the sheets in this file): Results = every paper; Flagged only "
             "= just the on_list + review papers to act on; Data quality = papers "
             "whose Semantic Scholar metadata looks suspect, plus those whose DOI "
             "was cross-checked against Crossref; Summary = counts + run details; "
             "Legend = this page. The thorough check adds an 'LLM review' column "
             "on Flagged only.",),
            ("NOT proof: Beall's List is an unofficial, archived (~2021), CONTESTED "
             "list, so read 'on_list' as \"appears on the list\", not \"is "
             "predatory\". See the Caveats at the bottom.",),
        ],
    },
    {
        "title": "Glossary — terms used in this report",
        "headers": None,
        "rows": [
            ("Semantic Scholar (\"S2\") — the scholarly search engine "
             "(semanticscholar.org) whose per-paper metadata is the INPUT to this "
             "check.",),
            ("Beall's List — an unofficial, archived, contested catalogue of "
             "alleged predatory publishers and journals (five lists; see below).",),
            ("Venue — the journal or conference a paper was published in; this is "
             "what the check classifies (not the paper's content).",),
            ("DOI — a paper's permanent identifier / link (e.g. 10.1002/rev3.3433).",),
            ("ISSN — the 8-digit number unique to a journal (e.g. 0079-2047).",),
            ("Domain — the venue's website host (e.g. mdpi.com); the most reliable "
             "match signal.",),
            ("Crossref — the publisher-deposited registry used by default "
             "(disable with --crossref off) to double-check Semantic Scholar's "
             "venue/ISSN and to settle uncertain 'review' verdicts.",),
        ],
    },
    {
        "title": "STATUS values (and the row color used in Results)",
        "headers": ("Status", "Confidence", "Meaning"),
        "swatch": True,
        "rows": [
            ("on_list", "high", "Venue matched a CORE Beall list (predatory "
             "publishers, standalone journals, or hijacked journals) by a "
             "high-confidence signal: exact domain, subdomain of a listed "
             "domain, exact ISSN, or exact name. Read as \"appears on Beall's "
             "List.\""),
            ("review", "verify", "A softer or contested match: fuzzy name, "
             "open-access PDF host, an alternate venue URL, or a WEAK list "
             "(vanity-press / misleading-metrics). Check by hand."),
            ("clean", "—", "The venue was identified and did NOT match any "
             "Beall entry."),
            ("no_venue", "—", "Preprint server (e.g. arXiv) or no venue "
             "metadata — there is no journal/publisher to classify."),
            ("error", "—", "The record could not be processed; the Python "
             "error is shown in the first row of the 'Match' column."),
        ],
    },
    {
        "title": "How the venue matched (shown inside the 'Why flagged' line)",
        "headers": ("How it matched (part of the 'Why flagged' text)",
                    "Result", "What it means — with a fixed real example"),
        # Rows are generated from SIGNAL_INFO so this table always matches the
        # phrases shown in the Match column.  Examples are constant (corpus-
        # independent) so every workbook documents the same illustrations.
        "rows": [
            (SIGNAL_INFO[k][0], SIGNAL_INFO[k][1], SIGNAL_INFO[k][2])
            for k in ("domain", "domain_subdomain", "issn", "name_exact",
                      "name_exact_doi", "name_fuzzy", "domain_alternate_url",
                      "domain_alternate_url_doi", "domain_open_access_pdf",
                      "domain_open_access_pdf_doi")
        ],
    },
    {
        "title": "'Beall list' — which list the matched entry came from",
        "headers": ("Beall list (shown in the 'Match' column)", "Weight", "Meaning"),
        "rows": [
            ("predatory publisher", "core list",
             "A predatory publisher — Beall's main list."),
            ("standalone predatory journal", "core list",
             "A single predatory journal that is not part of a larger publisher."),
            ("hijacked / cloned journal", "core list",
             "A fake journal impersonating a legitimate one by reusing its name "
             "and look (only the fake side is listed). A name-only match is "
             "downgraded to review, since the clone reuses the real journal's "
             "name; only a website match is asserted as on_list. Such a review is "
             "cleared to clean when the paper is proven not to be the counterfeit "
             "— its venue is the authentic journal's own site, or its DOI resolves "
             "(Crossref) to a real publisher that is neither the counterfeit nor "
             "on the list — with the reason kept on the Data-quality sheet."),
            ("vanity press (book publisher)", "weak — review only",
             "A book/monograph publisher (e.g. IGI Global). Not a journal-quality "
             "signal, so a match here is downgraded to review."),
            ("fake-metrics company", "weak — review only",
             "A company selling fake impact-factor metrics — not a venue at all, "
             "so a match is downgraded to review."),
            ("your own blacklist (not Beall's List)", "supplied for this run",
             "Not one of Beall's lists: a venue named in the blacklist given to "
             "this run, which the check treats as predatory because it was told "
             "to. Matched on the site, the ISSN or the exact name — whichever the "
             "entry carried — and asked before anything else, so it applies even "
             "to a venue the check would otherwise not classify at all, such as a "
             "preprint server. Nothing here reflects Beall's List; remove the "
             "entry and the row goes with it."),
        ],
    },
    {
        "title": "Layout of the Results / Flagged-only sheets",
        "headers": ("Column", "Rows (top -> bottom)", "Meaning"),
        "rows": [
            ("(blocks)", "4 rows per paper", "Each paper occupies a block that "
             "is 4 rows tall for every column EXCEPT 'Mentioned in', which grows "
             "to fit all its linked venues (so a paper with 10 linked venues is "
             "10 rows tall). Within a block each column's values are merged to "
             "fill it — a column with one value is a single cell spanning the "
             "whole block, never blank padding rows. Alternating blocks are "
             "lightly shaded so they are easy to tell apart."),
            ("#", "(merged)", "Sequential paper number; the cell spans the "
             "paper's whole block."),
            ("Status", "(merged)", "Classification of the paper (see the Status "
             "section); spans the block and is colored by status."),
            ("Paper specifications", "(header band)",
             "A grouping header over the next three columns (Paper, Semantic "
             "Scholar venue, Mentioned in) — everything describing the paper "
             "itself, as opposed to why it was flagged ('Match')."),
            ("Paper", "Title / Year / Source / DOI",
             "Paper identity, one field per row. 'Source' is which corpus JSON "
             "the paper came from. The DOI row is a link to the article at its "
             "publisher."),
            ("Semantic Scholar venue", "Venue / Type / Domain / ISSN",
             "The publication venue exactly as Semantic Scholar reports it, one "
             "field per row. 'Type' is journal/conference (conferences are out "
             "of scope); 'Domain' is the venue website host — the main match key "
             "— and links to the venue's site. (Named 'Semantic Scholar venue' to "
             "distinguish it from the 'Mentioned in' names beside it.)"),
            ("Mentioned in", "one row per linked venue",
             "Each journal / publisher / venue tied to the paper THAT HAS A WEB "
             "LINK, showing the venue name and linking to the venue's own site. "
             "(The venue type — journal/conference — is not repeated here; it is "
             "in the 'Type' field of the Semantic Scholar venue column.) Venues "
             "with no link are omitted, and there is one entry per distinct venue "
             "URL (canonical + alternates), so a merged-venue record shows every "
             "site it points at, each disambiguated by its host. This is the one "
             "column whose block grows to fit them all (6 linked venues -> a "
             "6-row block; the other columns keep their fields, merged to fit)."),
            ("Match", "Listed as / Beall list / Why flagged",
             "Why the paper was flagged, one field per row. 'Listed as' is the "
             "Beall's List entry that matched — its link opens THAT entry's own "
             "website (the listed/predatory venue), NOT this paper's venue; "
             "'Beall list' is which list it came from (see the 'Beall list' "
             "section); 'Why flagged' is a single plain-English line naming HOW "
             "it matched (see the 'How the venue matched' section) and the "
             "concrete listed entry. For clean / no_venue / error rows the first "
             "row shows a short note and the rest are blank."),
            ("LLM review", "(Flagged-only, LLM workbook)",
             "Present only on the Flagged-only sheet of bealls_llm_results.xlsx, "
             "and only for 'review' papers: the LLM's recommendation — Predatory "
             "(exclude) / Legitimate (keep) / Uncertain (check) — plus the entity "
             "it matched and a one-line reason, to help you resolve each review "
             "paper. The LLM never sets 'on_list' on its own."),
        ],
    },
    {
        "title": "The 'Data quality' sheet",
        "headers": ("Column", "Rows (top -> bottom)", "Meaning"),
        "rows": [
            ("(which papers)", "one block per paper",
             "Lists the papers whose Semantic Scholar record looks suspect — so a "
             "verdict above isn't trusted blindly — PLUS any paper whose DOI was "
             "checked against Crossref, showing what that check found. A row here "
             "is therefore not automatically a problem: it may simply be the "
             "record of a venue that Crossref confirmed or cleared. This sheet is "
             "omitted entirely when there is nothing at all to report."),
            ("Data-quality issue", "one row per issue",
             "The specific problem(s) found: no venue info, a malformed "
             "DOI/ISSN, leftover HTML codes in the name, a 'merged venue' (one "
             "record pointing at several publishers' sites), and — when the "
             "Crossref cross-check is enabled — a venue/ISSN that disagrees with "
             "the publisher-deposited record. For a 'merged venue' the check also "
             "looks up the paper's DOI in Crossref and names the journal it was "
             "actually published in, so you don't have to. Appearing here is a "
             "'check this by hand' flag, not proof the verdict is wrong."),
        ],
    },
    {
        "title": "Caveats — read before interpreting results",
        "headers": None,
        "rows": [
            ("Beall's List is an unofficial, archived, frozen (~2021), CONTESTED "
             "list. A match is an allegation as of the snapshot date, not proof "
             "a venue is predatory.",),
            ("Being on the list is contested. Frontiers, for example, is on the "
             "list but widely considered legitimate, so 'on_list' = \"appears "
             "on the list,\" not \"is predatory.\" (MDPI is in the site's "
             "'Excluded — decide after reading' section, so it is NOT flagged.)",),
            ("This check classifies the VENUE, not the paper's content/quality.",),
            ("The base check uses NO LLM (0 tokens). Every result is "
             "deterministic and auditable from the 'Match' column. The optional "
             "LLM pass only ADDS recommendations for the 'review' papers (the "
             "'LLM review' column); it never changes a deterministic verdict.",),
            ("no_venue is mostly arXiv/preprints, which have no journal "
             "publisher and so cannot be on Beall's List.",),
        ],
    },
]
