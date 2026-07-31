# Example corpus

[`sample_papers.json`](sample_papers.json) is a small, **synthetic** corpus of
eight Semantic Scholar records. It exists so that a fresh installation can be
verified without supplying real data, and so that the behaviours described in the
main [README](../README.md) can be observed rather than taken on trust.

The records are illustrative, not genuine research metadata: the titles state
what each record demonstrates. The venue fields — which are what the check
actually reads — are realistic, and the DOIs are real ones, so that the Crossref
path is genuinely exercised rather than short-circuited.

## Verifying an installation

```bash
python src/bealls_list_check/batch_check/batch_check.py examples/sample_papers.json -o out.csv
```

A successful run reports `Checked 8 paper(s)` and writes `out.csv`. To confirm the
offline path works as well, add `--crossref off`.

To exercise the full pipeline and the Excel writer instead, point the main check
at this folder:

```bash
export BEALLS_CORPUS_DIR=examples     # PowerShell: $env:BEALLS_CORPUS_DIR = "examples"
python src/bealls_list_check/bealls_list_check.py
```

The web app accepts the same file through **Step 2 · Provide the papers**.

## Expected output

Verified against the snapshot scraped 2026-06-10. Later snapshots may reclassify
individual venues.

| # | Demonstrates | `--crossref off` | Default (Crossref on) |
|---|---|---|---|
| 1 | The venue's own website is a listed site (`scirp.org`) — the strongest signal. | `on_list` | `on_list` |
| 2 | A mainstream journal that is not on the list. | `clean` | `clean` |
| 3 | A preprint, so there is no journal publisher to check. | `no_venue` | `no_venue` |
| 4 | The venue **name** matches a listed entry but the paper's own website does not corroborate it. | `review` | `on_list` |
| 5 | An established journal whose DOI confirms the venue. | `clean` | `clean` |
| 6 | A record carrying no venue information at all. | `no_venue` | `no_venue` |
| 7 | A **vanity-press** match (IGI Global) — a weak list, so it is never asserted. | `review` | `review` |
| 8 | An **alternate** web link points at a listed site, while the canonical one does not. | `review` | `on_list` |

Totals — offline: 1 `on_list`, 3 `review`, 2 `clean`, 2 `no_venue`. With Crossref:
3 `on_list`, 1 `review`, 2 `clean`, 2 `no_venue`.

Three rules from the main README are visible in that table:

- **Crossref resolves uncertainty.** Records 4 and 8 are `review` offline and
  `on_list` once the DOI is checked against the registry — that promotion is the
  whole point of the [resolve step](../src/bealls_list_check/resolve/README.md).
- **Weak lists are never asserted.** Record 7 stays `review` in both modes,
  because a vanity-press match is not a predatory-*journal* signal.
- **A name alone is not enough.** Record 4 is `review` offline precisely because
  nothing corroborated the name match; see
  [`match/`](../src/bealls_list_check/match/README.md).

Supplying a [whitelist](../src/bealls_list_check/user_lists/README.md) does not
add a verdict to this table; it removes rows from it. Whitelisting records 1 and
2's venues, for instance, yields a three-row report — record 8's venue shares
SCIRP's domain with record 1's — and the other five papers appear nowhere in it.
