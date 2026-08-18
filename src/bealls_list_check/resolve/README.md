# DOI-based resolution via Crossref

> Deep-dive companion to [`resolve.py`](resolve.py) and the Crossref client in
> [`data_quality.py`](../data_quality.py). The main
> [README](../../../README.md) links here.
> For the first-pass matching that *produces* the `review` verdicts, see
> [`match.md`](../match/README.md).

[`match.py`](../match/match.py) makes a first-pass verdict from Semantic Scholar metadata
alone. Some of its `review` results are *soft/contested* — a venue name matched
but the website differs, an alternate URL pointed at a listed site, a free PDF
sits on a listed site, or a hijacked-journal name matched. In each case the
paper's **DOI** is the authoritative tiebreaker: Crossref (the publisher-deposited
registry) records which journal that exact paper was really published in.

## What "using Crossref" actually means

The tool does **not** open the DOI link (`doi.org/…`) or fetch the publisher's
web page. It queries the **Crossref REST API by DOI** —
`GET https://api.crossref.org/works/{doi}` — and reads the deposited metadata
from the returned JSON. Everything funnels through one function,
`data_quality._crossref_lookup(doi)`; every other piece just consumes its result.

From the Crossref record it uses only four fields:

| Field | Used as |
|---|---|
| `container-title[0]` | journal name |
| `ISSN` | the strongest match signal |
| `resource.primary.URL` → host | the "publisher website" (a string comparison, **not** a page fetch) |
| `publisher` | publisher name (display only, for the audit note) |

## The one rule: act only on positive proof

A `review` is promoted to `on_list` **only** when the DOI confirms the listed
venue, and cleared to `clean` **only** when the DOI positively identifies the
paper's own, *unlisted* venue (by domain/ISSN — never by a shared name). Anything
ambiguous stays `review`. The check **never** clears on the weak signal "the real
publisher merely isn't listed" — a predatory publisher recorded under a different
domain would slip through that. **A false clear is the one error this check must
not make.**

`_crossref_matches()` accepts a match on any unambiguous field — ISSN, publisher
website, or exact journal name — and names which one for the audit trail. For a
`name_exact` or hijacked review the *name* signal is disabled (`allow_name=False`),
because the name is the very thing in doubt; only domain/ISSN can discriminate.

## The five resolvers

`resolve_review()` runs five resolvers over one result; each is a no-op unless the
row is the specific kind it handles. The first four settle a `review`:

| Resolver | Handles | Outcome |
|---|---|---|
| `_clear_hijacked_if_genuine` | a hijacked-by-name review | clear to `clean` only if the paper's venue **or** its DOI points to the *authentic* journal's own site (from Beall's "authentic journal" column); else stays `review`. |
| `_resolve_alternate_url` | a `domain_alternate_url` review | DOI's venue **is** the listed alternate → `on_list`; DOI positively identifies the paper's own unlisted venue → `clean`; else `review`. |
| `_resolve_open_access_pdf` | a `domain_open_access_pdf` review | DOI says the listed PDF host **published** it → `on_list`; DOI identifies the paper's own unlisted venue (PDF merely re-hosted) → `clean`; else `review`. |
| `_resolve_name_exact` | a `name_exact` review | DOI's venue **is** the listed journal (domain/ISSN only) → `on_list`; DOI identifies a different same-named unlisted venue → `clean`; else `review`. |

The fifth asks the opposite question, of the papers nobody flagged:

| Resolver | Handles | Outcome |
|---|---|---|
| `_flag_clean_if_crossref_venue_listed` | a `clean` verdict with a DOI | DOI's registered publisher **website** is listed → `on_list`; its registered **ISSN** is listed → `on_list`; only the registered **name** matches → `review`; otherwise untouched. |

### Why a `clean` verdict is re-checked at all

A `clean` verdict is only ever as good as the metadata behind it. `match.py` sees
Semantic Scholar's venue fields and nothing else, so when those are wrong — a
known and common failure — a paper published in a listed venue is reported
`clean` and no later stage looks again. Crossref records which venue actually
registered the DOI, and that is the one authority on the question.

Measured on 1,500 papers of a real corpus: 4 caught, every one a Frontiers
journal whose abbreviated Semantic Scholar name (`Frontiers Artif. Intell.`)
matches no list entry while its DOI resolves to the listed `frontiersin.org`.

The tier decides the verdict, not just the wording. A domain and an ISSN each
identify exactly one venue, so they are asserted; a *name* is shared between
legitimate and predatory journals — that is what a hijacked journal is — so a
name-only hit is raised for a human rather than asserted.

This is the only resolver that makes a verdict **stricter**, which follows the
positive-proof rule rather than breaking it: acting on proof that a clean paper
belongs to a listed venue *removes* a false clear instead of creating one, and
its worst case is a false alarm carrying the evidence to dismiss it. It never
touches a paper an earlier resolver deliberately cleared (`cleared_reason`) —
that decision was made from the DOI too, on evidence specific to that paper.

Every promotion, clearance or re-flag records a plain-English note on the **Data
quality** sheet explaining the doubt and how the DOI resolved it.

## What is never asked of Crossref

Two DOI prefixes' worth of requests are skipped rather than sent, because their
answer is known: a DOI registered with **DataCite** (arXiv `10.48550`, Zenodo,
figshare, Dryad, OSF) is absent from Crossref *by definition*. Asking is a
guaranteed-wasted round-trip, and reporting the miss as "not found in the
publisher registry" reads as a red flag when it is the correct and expected
answer for a preprint. On a machine-learning corpus this is not a detail: 42% of
one 18,096-DOI corpus was arXiv.

Lookups are also **batched and fetched concurrently** rather than made one at a
time in the middle of classifying each paper — see `bealls_list_check.CROSSREF_BATCH`
and `data_quality.prefetch()`. Setting `CROSSREF_MAILTO` in `.env` identifies the
tool to Crossref and moves it into the *polite pool* (10 requests/second with
concurrency 3, against 5/second with concurrency 1 anonymous); a shared rate
limiter holds just under whichever ceiling applies. Together these took an
18,482-paper run from ~38 minutes to ~20, with more checking done, not less.

## Data-quality resolution of "merged venues"

Separately, when Semantic Scholar has fused two same-named journals into one
record (its venue points at ≥ 2 different publishers' sites),
`data_quality._resolve_real_journal()` looks the DOI up in Crossref and names the
journal the paper was *actually* published in — and labels which of the merged
sites is CORRECT and which are WRONG — so you don't have to resolve it by hand.

`crossref_flags()` adds a second data-quality signal: it compares Semantic
Scholar's venue name and ISSN against Crossref's record and flags any
disagreement.

## When it runs — default-on with an offline fallback

- Crossref is used **by default** (`--crossref all`). At startup the run probes
  Crossref once (`resolve_crossref_mode()` → `crossref_reachable()`); if it's
  unreachable (no internet), the whole run silently degrades to fully offline, so
  it never stalls or fails for lack of a connection.
- `--crossref flagged` restricts network lookups to `on_list`/`review` rows.
- `--crossref off` forces a fully offline run even when online.

## Reliability of the mechanism

The lookup is hardened: it **retries** transient failures (429/5xx, timeouts) with
exponential backoff, **caches** every result per-DOI (including 404 → `None`), and
trips a **circuit breaker** after 3 consecutive failures so a dead connection
degrades to "left in review" instead of stalling on every paper. Setting
`CROSSREF_MAILTO` in `.env` opts into Crossref's faster "polite pool."

- **ISSN** is the strongest, most reliable signal.
- The **publisher-website** match is the weakest link — `resource.primary.URL` is
  populated inconsistently by publishers — but its weakness costs *coverage* (a
  missed resolution → stays `review`), not *correctness*.
- Coverage is bounded by DOI presence and **Crossref registration**: DOIs
  registered with DataCite or other agencies return 404 and stay `review`.

Net: **very reliable as a safety mechanism** (it won't make false clears — every
failure mode degrades toward `review`), and **moderately complete** as a
resolution mechanism (many reviews legitimately stay `review` when there's no
positive proof to be had).

## Worked example (a hijacked-by-name review)

Suppose Semantic Scholar reports a paper's venue as *Phyton: Annales Rei
Botanicae* — the name of a listed **hijacked/cloned** entry, a real journal
(authentic site `verlag-berger.at`) that a predatory site (`engineeronline.net`)
once impersonated by copying its name. Because the match is by *name*, the check
can't tell the genuine journal from the counterfeit, so it starts as a `review`.
(A bare venue name like just `Phyton` wouldn't match at all — the exact-name tier
needs ≥ 2 words — and would stay `clean`.) The check then reads the paper's DOI
(`10.32604/phyton.2024.050556`) in Crossref: it was published by **Tech Science
Press** — neither the counterfeit nor the recorded authentic journal, but a
*third* journal that merely shares the short name "Phyton." Because it can't
*prove* this is the genuine journal, it **stays `review`** for a human. A
hijacked-by-name paper is cleared to `clean` only when the venue or DOI points to
the authentic journal's own site — positive proof.
