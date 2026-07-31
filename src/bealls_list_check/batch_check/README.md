# Batch check — CSV output for server integration

> Deep-dive companion to [`batch_check.py`](batch_check.py). The main
> [README](../../../README.md) links here.
> For how a verdict is reached, see [`match/`](../match/README.md) and
> [`resolve/`](../resolve/README.md).

A single command that runs the same deterministic engine as the main check but
with machine-readable input and output, so it can be called from a server or a
script without touching the Excel pipeline:

```bash
python src/bealls_list_check/batch_check/batch_check.py papers.jsonl -o results.csv
```

| Flag | Meaning |
|---|---|
| `input` (positional) | `.jsonl`/`.ndjson` — one paper record per line — or `.json` — an array of records. |
| `-o, --output` | CSV path to write. Default: the input path with a `.csv` suffix. |
| `--crossref on\|off` | Settle uncertain `review` verdicts via the paper's DOI in Crossref. Default `on`; falls back to `off` automatically when Crossref is unreachable, so the command never stalls or fails for lack of internet. |

## Input structure

Each record is a **Semantic Scholar paper object** exactly as returned by the
[Graph API](https://api.semanticscholar.org/api-docs/graph) with the fields
`paperId,externalIds,venue,publicationVenue,openAccessPdf` — API responses can
be written line-by-line into the `.jsonl` without any transformation. Unknown
extra fields (`title`, `abstract`, …) are ignored, so full API records are fine
as-is.

| Field | Type | Role |
|---|---|---|
| `paperId` | string | Echoed to the `SS-ID` column. Not used for matching; empty in the CSV if absent. |
| `externalIds.DOI` | string | Echoed to the `DOI` column, and used (with `--crossref on`) to settle uncertain verdicts against the Crossref registry. |
| `publicationVenue.url` | string | **Strongest venue signal** — the venue's website, compared against the listed sites. |
| `publicationVenue.issn` | string | Exact-ISSN signal (formats like `"1234-5678"` or `"12345678, 8765-4321"` are handled). |
| `venue`, `publicationVenue.name` | string | Venue name, for the exact- and fuzzy-name signals. |
| `publicationVenue.alternate_names` | list of string | Additional name candidates. |
| `publicationVenue.alternate_urls` | list of string | Alternate venue sites (a hit here is `review`-tier, never asserted). |
| `publicationVenue.type` | string | Optional; echoed into the audit trail only. |
| `openAccessPdf.url` | string | PDF host (a hit here is `review`-tier, never asserted). |

Every field is optional — a missing field only removes that signal. For a
meaningful verdict a record should carry **at least one venue signal**
(`publicationVenue.url`, `.issn`, or a name); a record with none is classified
`no_venue`. A minimal useful line:

```json
{"paperId": "00072685f1221b6bb51b45c4d51c7ada3287350d", "externalIds": {"DOI": "10.1093/ced/llae119"}, "venue": "Clinical and Experimental Dermatology", "publicationVenue": {"name": "Clinical and Experimental Dermatology", "issn": "0307-6938", "url": "http://onlinelibrary.wiley.com/journal/10.1111/(issn)1365-2230"}}
```

Files are read as UTF-8 (a leading BOM is tolerated). Blank lines in a `.jsonl`
are ignored. A malformed line aborts the run with a nonzero exit code and a
message naming the line — a caller can therefore treat any nonzero exit as
"bad input or setup" and any zero exit as "the CSV is complete".

## Output structure

A UTF-8 CSV with a header row and **exactly one row per input record, in input
order** (no deduplication — row *n* + 1 of the CSV always answers line *n* of
the `.jsonl`):

```csv
SS-ID,DOI,Status
00072685f1221b6bb51b45c4d51c7ada3287350d,10.1093/ced/llae119,clean
```

| Column | Content |
|---|---|
| `SS-ID` | The record's `paperId`, echoed verbatim (empty if the record has none). |
| `DOI` | The record's `externalIds.DOI`, echoed verbatim (empty if none). |
| `Status` | The verdict, one of the values below. |

`Status` takes one of five values (the main pipeline's whitelist cannot
occur here, as the batch command has no whitelist):

| Status | Colour | Meaning |
|---|---|---|
| `on_list` | 🔴 red | The venue matches a Beall's List entry by a high-confidence signal (website, exact ISSN, or corroborated exact name). Read as *"appears on Beall's List"* — not proof the venue is predatory. |
| `review` | 🟠 orange | A weak or uncertain match a human should verify. |
| `clean` | 🟢 green | The venue was identified and is **not** on the list. |
| `no_venue` | ⚪ grey | A preprint, or a record with no venue signals — nothing to check. |
| `error` | ⚫ dark | The record could not be processed (it still gets its row). |

A CSV is plain text and carries no colour itself; the **Colour** column above is
the canonical per-status colour the server (or a spreadsheet) should apply when
it *displays* a status, so it stays consistent with the tool's Excel report. The
exact values are defined once in [`config.py`](../config.py)
(`STATUS_FILL_COLORS`); the Streamlit UI's batch mode applies the very same
colours to its status preview and to its colour-coded `.xlsx` download.

The CSV carries the verdict only. For the evidence behind each verdict — which
signal matched, why a row is `review`, data-quality warnings — run the main
check ([`bealls_list_check.py`](../bealls_list_check.py)), which writes a fully
audited Excel workbook.

## What runs under the hood

Per record: `match_paper()` (the [match tiers](../match/README.md)), then —
with `--crossref on` — `resolve_review()` (the
[DOI resolvers](../resolve/README.md)), which can promote a `review` to
`on_list` or clear it to `clean` on positive proof from the paper's DOI. The
input data-quality validation and the Excel writer are skipped: they do not
affect the verdict, and this command's output is only the three columns.
