# What a run gives back

> Deep-dive companion to [`results.py`](results.py). The
> [web app's README](../README.md) gives the short version and links here.

## What you get back

A result survives a change of selection in Step 1 and stays labelled with the
check that produced it, together with a note that a fresh run is needed for the
newly selected one. Nothing is silently relabelled.

### Fast and thorough checks

A success banner, a **download button** for the workbook, and a preview of its
**first sheet, first 40 rows**, with the sheet names listed above it. Below that,
a **Run log** expander holds the check's console output — including the
data-quality report — truncated to the last 8,000 characters, and it opens
automatically when a run fails.

![A finished fast check: the workbook preview, and the offer to add the CSV](../static/ui-report.png)

#### Adding the CSV afterwards

A workbook result is followed by **Also produce the batch CSV**, which runs the
batch check over the same papers and appends the CSV, its colour-coded Excel twin
and the status chips beneath the workbook.

It is an action rather than an automatic by-product because the CSV **cannot be
derived from the workbook**. The two disagree on record handling by design: the
workbook is deduplicated on `paperId` → `title`, whereas the CSV carries one row
per input record, in input order. Producing it therefore means running the
deterministic pass a second time, repeating every Crossref lookup — a cost worth
paying on request and not worth imposing on every run. For the same reason the
CSV ignores any whitelist or blacklist, and never carries the thorough check's
LLM verdict; the button's caption states all of this before it is pressed.

The result is discarded whenever the check selection changes or a new run starts,
so a CSV is never shown next to a workbook it did not come from.

### Batch CSV

Per-status **count chips** in the canonical status colours, then two downloads and
a 100-row preview whose Status cells are shaded to match.

- **`bealls_status.csv`** — the machine-readable `SS-ID, DOI, Status` file, one
  row per input record in input order.
- **`bealls_status.xlsx`** — the same three columns with the status colour
  applied. A CSV cannot carry colour; this is its human-readable twin.

![Batch CSV mode, with colour-coded statuses](../static/ui-batch.png)

The colours come from `STATUS_FILL_COLORS` in
[`config.py`](../../src/bealls_list_check/config.py), so the chips, the preview, the
Excel twin and the main report cannot drift apart.
