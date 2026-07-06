# Architecture

This document describes the structure of the Beall's List predatory-venue check:
the processing pipeline, the responsibility of each module, the shared data
structure, and the design rules that the code is expected to uphold. It is
intended as a reference for developers extending or maintaining the project.

For an end-user description of *what the tool does and how to run it*, see the
[README](README.md). This document covers *how the code is organised*.

---

## 1. Overview

The tool reads a corpus of **Semantic Scholar paper records** (JSON) and, for each
paper, classifies its **publication venue** against a vendored snapshot of
**Beall's List** of allegedly predatory venues. The output is a single **Excel
workbook** in which every paper receives one of six verdicts — `on_list`,
`review`, `clean`, `no_venue`, `out_of_scope`, or `error` — accompanied by the
evidence behind that verdict so it can be audited manually.

The base check is fully deterministic and performs no language-model inference;
every verdict is explainable from the input data. An optional second pass
(`bealls_llm_check.py`) adds language-model recommendations for the `review`
papers only and never overrides a deterministic verdict.

> A verdict of `on_list` means "the venue appears on Beall's List," not "the venue
> is predatory." Beall's List is unofficial, archived (~2021), and contested. See
> the README's "Limitations and responsible use" section.

---

## 2. Processing pipeline

The control flow is defined in `bealls_list_check.py` (the *conductor*, ~200
lines). It performs the following stages in order:

```
   bealls_list_check.py · main()  —  conductor + command-line interface
        |
   1.  load the snapshot ──────────  build_index()        -> BeallIndex      (match.py)
   2.  for each corpus paper ──────  load_corpus()         -> paper records
   3.    classify the venue ───────  match_paper()         -> result dict     (match.py)
   4.    validate the input ───────  input_quality.validate_input()           (common/)
   5.    resolve 'review' (DOI) ───  resolve_review()      [Crossref; on by default] (resolve.py)
   6.  render the workbook ────────  save_workbook()       -> results/*.xlsx   (report.py)
```

Each stage reads from, or writes to, a single **result dictionary** per paper
(Section 4). That dictionary is the common data structure passed between modules.

The Crossref-backed stage (5) and the Crossref branch of stage (4) perform network
requests. They run **by default** (`--crossref all`): `main()` probes Crossref
once at startup (`resolve_crossref_mode()` → `data_quality.crossref_reachable()`)
and, if it is unreachable (no internet), degrades the run to `--crossref off` so
the entire run is offline. Pass `--crossref off` to force offline even when online.

---

## 3. Module reference

All modules reside in `src/bealls_list_check/`. Each has a single responsibility.
Three (`match`, `resolve`, `user_lists`) live in their **own folder** with a
`README.md` deep-dive, linked below; the rest are flat files in the package
directory. (The folders keep a bare-name import style via a small path shim at the
top of each moved module and each entry script.)

| Module | Responsibility |
|---|---|
| `bealls_list_check.py` | Conductor and command-line interface. Orchestrates the pipeline; contains no classification or rendering logic of its own. |
| `match/match.py` ([`README`](src/bealls_list_check/match/README.md)) | Determines each paper's verdict from Semantic Scholar metadata (domain, ISSN, exact-name, and fuzzy-name signals). Defines `BeallIndex` and the result dictionary. |
| `resolve/resolve.py` ([`README`](src/bealls_list_check/resolve/README.md)) | Upgrades or clears a `review` verdict using the paper's DOI via Crossref (four resolvers behind `resolve_review`). Network-dependent; invoked whenever Crossref is enabled (the default when online). |
| `data_quality.py` | Structural and consistency checks on each Semantic Scholar record, and the cached Crossref client through which all network lookups pass. |
| `descriptions.py` | All human-readable text presented in the workbook: signal explanations, Beall-list names, and the Legend sheet content. Contains no I/O and no layout code. |
| `report.py` | Renders the Excel workbook with openpyxl (sheets, per-paper blocks, merges, fonts, colours). Contains no classification logic; its text is sourced from `descriptions.py`. |
| `normalize.py` | Canonicalisation of names, hosts, and ISSNs so that comparisons are consistent. |
| `config.py` | File paths and matching parameters (fuzzy threshold, preprint domains, status colours, and similar constants). |
| `user_lists/user_lists.py` ([`README`](src/bealls_list_check/user_lists/README.md)) | Handling of the optional `--whitelist` (scope filter) and `--blacklist` (additional predatory venues). |
| `scrape_bealls_list.py` | Produces the vendored `data/bealls_snapshot.json` from the archived Beall's List. Run only when refreshing the snapshot. |
| `bealls_llm_check.py` | Optional language-model pass over the `review` papers. Reuses the conductor (`import bealls_list_check as det`) for matching and for the workbook writer. |
| `batch_check/batch_check.py` ([`README`](src/bealls_list_check/batch_check/README.md)) | Machine-readable entry point for server integration: reads one `.jsonl`/`.json` file of Semantic Scholar records, writes a CSV of `SS-ID, DOI, Status` (one row per record, input order). Reuses `build_index`/`resolve_crossref_mode` from the conductor plus `match_paper` and `resolve_review`; skips the data-quality validation and the Excel writer. |

The separation of `descriptions.py` and `report.py` is deliberate: report text can
be revised without touching layout code, and layout can be revised without reading
prose. The two were previously a single ~940-line module.

---

## 4. The result dictionary

`match.py` constructs one dictionary per paper (`_blank_result`). All downstream
stages read and update it. The principal fields are:

```python
{
  # Identity and echoed input (retained for the audit trail)
  "source_file", "paperId", "title", "year", "doi", "paper_url",
  "venue", "venue_type", "venue_domain", "venue_url", "canonical_domain", "issn",
  "mentions",            # [(display_text, url)] — the linked venues ("Mentioned in")

  # Verdict (set by match.py, possibly revised by resolve.py)
  "status",              # on_list | review | clean | no_venue | out_of_scope | error
  "list_source",         # which Beall list matched (publishers / hijacked / ...)
  "matched_name",        # the listed entry that matched
  "matched_url",         # that entry's own website (the listed/predatory site)
  "matched_on",          # the signal: domain / issn / name_exact / name_fuzzy /
                         #             domain_alternate_url / *_doi / ...
  "review_reason",       # plain-English explanation of a 'review' verdict
  "alt_source_urls",     # alternate URL(s) that matched a listed site
  "authentic_url", "authentic_domain",   # for hijacked entries: the genuine journal
  "cleared_reason",      # set when resolve.py clears a review back to clean

  # Attached by the conductor
  "data_quality",        # [str] warnings from input_quality (rendered on its own sheet)
}
```

Together, `status` and `matched_on` record both the outcome and the reason for it,
and form the basis of the report.

---

## 5. Design principles

The following invariants are intended to be preserved by any change:

1. **Crossref by default, with an offline fallback.** A run uses Crossref by
   default (`--crossref all`) but degrades to fully offline when Crossref is
   unreachable (probed once at startup) or when `--crossref off` is passed — so a
   run never stalls or fails for lack of internet. All Crossref access is confined
   to `resolve.py` and the Crossref branch of `data_quality.py`, both gated behind
   the effective `--crossref` mode, and every offline-degraded path leaves the
   affected papers exactly as a no-network run would (typically `review`).

2. **Positive proof in DOI resolution.** In `resolve.py`, a `review` is promoted to
   `on_list` only when the DOI confirms the listed venue, and cleared to `clean`
   only when the DOI positively identifies the paper's own, unlisted venue (by
   domain or ISSN, never by a shared name). Ambiguous cases remain `review`. A
   false clear is the error the check is designed to avoid.

3. **Name matches require corroboration.** An exact venue-name match is asserted as
   `on_list` only when the paper's own website (or ISSN) corroborates it, or when
   the listed entry records no website at all. Otherwise it is downgraded to
   `review`, because distinct journals frequently share a name (`match.py`,
   `_classify`).

4. **`clean` means "not on Beall's List," not "reputable."** The wording in
   `descriptions.py` states this explicitly and should remain.

5. **Single source of truth for wording.** The Match column and the Legend are both
   generated from `SIGNAL_INFO` in `descriptions.py`, ensuring they cannot diverge.
   Signal explanations should not be duplicated in `report.py`.

6. **The report writer is shared.** `save_workbook(...)` accepts keyword-only
   extensions (`flagged_extra_columns`, `token_usage`, `llm_stats`) so that
   `bealls_llm_check.py` produces the same workbook with an additional column,
   without a second writer.

7. **Stable public interface on the conductor.** `bealls_llm_check.py` depends on
   `build_index`, `classify_corpus`, and `save_workbook` being importable from
   `bealls_list_check`. The last is re-exported from `report.py`. Moving any of
   these requires preserving its importability or updating the consumer.

---

## 6. Extension guide

| Intended change | Location |
|---|---|
| Revise user-facing text (a signal explanation, a Legend entry) | `descriptions.py` |
| Change colours, column widths, sheet composition, or block layout | `report.py` |
| Add or modify a match signal | `match.py` |
| Change DOI-based confirmation or clearing behaviour | `resolve.py` |
| Add an input data-quality check, or modify Crossref access | `data_quality.py` (and `common/input_quality.py`) |
| Add a command-line flag or pipeline stage | `bealls_list_check.py` |
| Adjust the fuzzy threshold, preprint domains, status colours, or paths | `config.py` |
| Change name, host, or ISSN normalisation | `normalize.py` |

---

## 7. Running and verifying

```bash
# Refresh the vendored snapshot (infrequent)
python src/bealls_list_check/scrape_bealls_list.py

# Deterministic check (no language model). Uses Crossref by default when online,
# and falls back to fully offline automatically when there's no internet.
python src/bealls_list_check/bealls_list_check.py

# Narrow the Crossref work, or force a fully offline run:
python src/bealls_list_check/bealls_list_check.py --crossref flagged   # on_list/review DOIs only
python src/bealls_list_check/bealls_list_check.py --crossref off       # never contact Crossref

# Optional language-model pass over the 'review' papers (requires provider
# configuration; see .env.example)
python src/bealls_list_check/bealls_llm_check.py

# Batch mode for server integration: .jsonl in, CSV (SS-ID, DOI, Status) out
python src/bealls_list_check/batch_check/batch_check.py papers.jsonl -o results.csv
```

Output is written to `src/bealls_list_check/results/` (not tracked by git).

Because the module layout is the result of a behavior-preserving refactor, a
suitable regression check for a future change is to run `--crossref off` before
and after the change and compare the two workbooks; they should match cell for
cell except for the "Check run (local)" timestamp and the workbook's own
created/modified metadata.

---

## 8. Recommended reading order

1. `bealls_list_check.py` — `main()` defines the complete pipeline.
2. `match.py` — `_blank_result()` defines the result dictionary; `match_paper()`
   and `_classify()` define how a verdict is reached.
3. `descriptions.py` — `SIGNAL_INFO` defines the meaning of each signal, reused by
   both the report and the Legend.
4. A generated workbook's **Legend** sheet documents the output in full.
