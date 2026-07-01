# DOI-based resolution via Crossref

> Deep-dive companion to [`resolve.py`](resolve.py) and the Crossref client in
> [`data_quality.py`](data_quality.py). The main
> [README](../../README.md#9-data-quality-and-the-crossref-cross-check) links here.
> For the first-pass matching that *produces* the `review` verdicts, see
> [`match.md`](match.md).

[`match.py`](match.py) makes a first-pass verdict from Semantic Scholar metadata
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

## The four resolvers

`resolve_review()` runs four resolvers over one result; each is a no-op unless the
row is the specific kind of `review` it handles:

| Resolver | Handles | Outcome |
|---|---|---|
| `_clear_hijacked_if_genuine` | a hijacked-by-name review | clear to `clean` only if the paper's venue **or** its DOI points to the *authentic* journal's own site (from Beall's "authentic journal" column); else stays `review`. |
| `_resolve_alternate_url` | a `domain_alternate_url` review | DOI's venue **is** the listed alternate → `on_list`; DOI positively identifies the paper's own unlisted venue → `clean`; else `review`. |
| `_resolve_open_access_pdf` | a `domain_open_access_pdf` review | DOI says the listed PDF host **published** it → `on_list`; DOI identifies the paper's own unlisted venue (PDF merely re-hosted) → `clean`; else `review`. |
| `_resolve_name_exact` | a `name_exact` review | DOI's venue **is** the listed journal (domain/ISSN only) → `on_list`; DOI identifies a different same-named unlisted venue → `clean`; else `review`. |

Every promotion or clearance records a plain-English note on the **Data quality**
sheet explaining the doubt and how the DOI resolved it.

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
