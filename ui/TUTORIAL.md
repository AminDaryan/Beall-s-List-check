# Tutorial — the web app, end to end

> A guided path through the app: install it, run it once on the bundled example
> corpus where the expected answer is known, then run it on real papers. Every
> step states what should appear on screen, so a run can be verified rather than
> assumed.
>
> This walkthrough is also the app's **Tutorial** page — the file is rendered
> there, so the two cannot drift apart. The reference companion is
> [ui/README.md](README.md), which documents every control and every difference
> from the command line; the project as a whole is described in the main
> [README](../README.md).

## What the tool answers

For each paper, one question: **is the journal or publisher it was published in
listed on [Beall's List](https://beallslist.net) of potentially predatory
venues?**

Three things follow from that, and they are worth stating before the first run:

- The tool judges the **venue**, never the paper. A match means *"this venue
  appears on the list"* — not that the research is bad.
- Beall's List is unofficial, archived and contested, so a match is an
  **allegation as of the snapshot date**. Uncertain matches are flagged for a
  human instead of being asserted.
- The input is **Semantic Scholar paper records** — JSON objects carrying a
  `publicationVenue` — not links, titles or PDFs.

## 1 · Install, once

Requires **Python 3.9 or newer** and, for setup only, an internet connection.
From the repository root:

```powershell
.\setup.ps1
```

```bash
bash ./setup.sh
```

The script creates `.venv`, installs the dependencies, and copies `.env.example`
to `.env`. **No API keys are required** for anything in this tutorial except
[the thorough check](#6--the-thorough-check-optional). Re-running it is safe: an
existing `.env` is never overwritten.

If PowerShell refuses to run the script (*"running scripts is disabled on this
system"*), use `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

## 2 · Start the app

```powershell
.\run_ui.ps1
```

```bash
bash ./run_ui.sh
```

The launcher installs Streamlit into the existing `.venv` on first use, then
prints a URL — `http://localhost:8501`, unless that port is taken, in which case
Streamlit selects the next free one. Open the URL that was printed, not the one
in this sentence.

The app is a **local, single-user** interface: no server, no authentication, no
accounts. It runs the same scripts the command line runs, so both produce
identical reports. Stopping the launcher (Ctrl+C) stops the app; closing the
browser tab does not.

![The app after startup, with the example corpus loaded](screenshots/ui-main.png)

The sidebar holds the page links — **Run a check** and **Tutorial** — a summary
of what the tool does, and an **Environment** panel naming the configured LLM
provider and model. That panel matters only for the thorough check and stays
collapsed until it is needed.

## 3 · A first run, on the example corpus

The repository ships an eight-record corpus whose expected result is published,
so a fresh installation can be verified without supplying real data. It exercises
every verdict except `out_of_scope`.

Every step carries a header bar — a numbered badge, the title, and a pill on the
right naming that step's live answer. The pill is green when the step is settled
and amber when it is what holds the run up, so the four of them read as a
checklist of what still has to be decided.

**Step 1 · Choose a check.** The three checks are laid out as a comparison table,
one row each, with a column per attribute they differ on: **Cost**, **Records**
and **Returns**. Hover any of those pills for the detail behind it — which file
comes back, what a run is charged for, whether repeated records are merged.
Select **⚡ Fast check** — rules only, no API keys, no cost. The selected row is
tinted and its button turns solid; the rest of the page is unaffected by the
choice.

**Step 2 · Provide the papers.** Under *Provide papers via*, select **Example
corpus**. The app confirms `✅ Loaded 8 paper(s) — ready to run` and opens the
**Input data quality** panel, which reports **1 of 8 paper(s) flagged** — record
6, which deliberately carries no venue information at all. That panel is an
offline inspection of the metadata *before* anything runs, and it is downloadable
as `input_data_quality.xlsx`.

**Step 3 · Options.** Leave them alone. Crossref is on by default, which is the
recommended setting — and which the step's pill confirms without opening it.

**Step 4 · Run.** A row of pills restates what is about to run — the check, the
number of papers, whether Crossref is on. Press **▶ Run check**. A progress bar
and the latest log line appear; the fast check has no per-item counter, so the
bar pulses rather than filling. Eight records complete in a few seconds.

### What should come back

A success banner, a **download button** for `bealls_list_results.xlsx`, and a
preview of the workbook's first sheet. Verified against the snapshot scraped
2026-06-10 — later snapshots may reclassify individual venues — the eight records
resolve as:

| # | What the record demonstrates | Crossref on (default) | Crossref off |
|---|---|---|---|
| 1 | The venue's own website is a listed site — the strongest signal. | `on_list` | `on_list` |
| 2 | A mainstream journal that is not on the list. | `clean` | `clean` |
| 3 | A preprint: no journal publisher to check. | `no_venue` | `no_venue` |
| 4 | The venue *name* matches, but the paper's own website does not corroborate it. | `on_list` | `review` |
| 5 | An established journal whose DOI confirms the venue. | `clean` | `clean` |
| 6 | No venue information at all. | `no_venue` | `no_venue` |
| 7 | A vanity-press match — a weak list, so never asserted. | `review` | `review` |
| 8 | An alternate web link points at a listed site; the canonical one does not. | `on_list` | `review` |

Totals with Crossref: 3 `on_list`, 1 `review`, 2 `clean`, 2 `no_venue`. Those
numbers are the installation check — if they match, the pipeline works end to
end, Crossref included. The full commentary is in
[examples/README.md](../examples/README.md).

## 4 · Reading what comes back

![A finished run, with the workbook preview](screenshots/ui-report.png)

The preview shows the **first 40 rows of the first sheet**; the workbook itself
carries five sheets, and the download button hands over the whole file.

| Sheet | Contents |
|---|---|
| **Results** | Every paper, in corpus order, coloured by status, with the evidence behind each verdict. |
| **Flagged only** | Just the `on_list` and `review` rows, `on_list` first. The thorough check adds its column here. |
| **Data quality** | Papers whose record warranted a warning. Absent entirely when nothing was flagged — that is a clean result, not a fault. |
| **Summary** | Counts per status, per match signal and per list, plus run metadata. |
| **Legend** | A plain-language data dictionary for every column and value. |

Every paper has exactly one **status**:

| Status | Colour | Meaning |
|---|---|---|
| `on_list` | 🔴 red | Strong match — the venue's own website, an exact ISSN, or a corroborated exact name, on a core list. |
| `review` | 🟠 orange | Weak or uncertain match. Needs a human; not an accusation. |
| `clean` | 🟢 green | The venue was identified and is **not** on the list. |
| `no_venue` | ⚪ grey | A preprint or a record with no venue — nothing to check. |
| `out_of_scope` | 🔵 blue | Only with a whitelist: the venue was not in it, so it was skipped. |
| `error` | ⚫ dark | The record could not be processed; the row states why. |

Two properties of the workbook regularly surprise a first reader: **each paper
occupies a four-row merged block** under a two-row header, so the row count is
not the paper count; and `clean` papers can appear on the *Data quality* sheet,
because the Crossref audit trail is written there as well.

Below the result, a **Run log** expander holds the check's console output. It
opens automatically when a run fails.

## 5 · Running the check on real papers

Real input is a JSON file of Semantic Scholar records, obtained from the
[Semantic Scholar API](https://api.semanticscholar.org) or exported from a
literature-search pipeline. In Step 2, select **Upload file** and pick the `.json`,
or **Paste JSON** and paste it directly. Accepted shapes:

- a **JSON list** of paper objects, or
- a **single** paper object, which is wrapped into a list automatically.

`.jsonl` is **not** accepted in the app — convert it to an array first, or use
`batch_check.py` on the command line, which reads `.jsonl`, `.ndjson` and `.json`.
Streamlit's upload limit is 200 MB per file.

The fields that drive the check are the venue ones. A minimal record:

```json
{
  "title": "Example paper",
  "venue": "Journal of X",
  "year": 2023,
  "externalIds": { "DOI": "10.1234/x" },
  "publicationVenue": {
    "name": "Journal of X",
    "type": "journal",
    "url": "https://www.scirp.org/journal/x",
    "issn": "1234-5678",
    "alternate_urls": []
  }
}
```

Records repeated across a corpus are counted once — deduplicated on `paperId`,
falling back to `title`. Verdicts are only as good as this metadata, which is why
the **Input data quality** panel appears before the run and why Crossref
cross-checking is on by default.

Expect a real run to take **minutes rather than seconds**: with Crossref enabled
the check contacts the registry once per distinct DOI. Setting `CROSSREF_MAILTO`
in `.env` moves those lookups into Crossref's faster "polite pool".

## 6 · The thorough check (optional)

The thorough check runs the fast check, then asks an LLM for a **keep / exclude /
uncertain** recommendation on each `review` venue. It never overrides a
deterministic verdict; the recommendation appears as an **LLM review** column on
the *Flagged only* sheet of a second workbook, `bealls_llm_results.xlsx`.

It is the only part of the tool that needs credentials and the only part that
costs money. Before using it:

1. Set `LLM_PROVIDER` in `.env` — `azure` (the default), `openai`, `anthropic`,
   `mistral`, `gemini` or `ollama` (a local server: no key, no cost).
2. Fill in that provider's key, and put the model name in
   `AZURE_OPENAI_DEPLOYMENT` — for **every** provider, not just Azure. The
   variable name is historical.
3. Restart the app. `.env` is read once, at startup.

Selecting the check verifies that configuration and reports a missing key or
endpoint by name, *before* a run is paid for. The verdict is a configuration
check, not a live call: it confirms that the values are present, not that they
are valid.

Cost scales with the number of **distinct `review` venues**, not papers — the
pass groups the rows by venue and makes one call per group. The app assesses
every one of them; there is no `--limit` in the browser. For a capped trial run,
use the command line: `python src/bealls_list_check/bealls_llm_check.py --limit 50`.

## 7 · Batch CSV, for scripts and servers

![Batch CSV mode, with colour-coded statuses](screenshots/ui-batch.png)

The third check returns `SS-ID, DOI, Status` — **one row per input record, in
input order, with no deduplication** — plus a colour-coded Excel twin, since a
CSV cannot carry colour. It classifies every record and therefore takes no
whitelist or blacklist.

A workbook result is followed by **Also produce the batch CSV**, which produces
the same file without returning to Step 1. It is a button rather than an
automatic by-product because the CSV cannot be derived from the workbook: the
workbook is deduplicated while the CSV keeps every record, so producing it means
running the deterministic pass — and its Crossref lookups — a second time.

## 8 · Narrowing or extending the list

Step 3 holds two optional boxes, available on the fast and thorough checks:

- **Whitelist** — restrict the run to these venues. Everything else becomes
  `out_of_scope`.
- **Blacklist** — treat these venues as predatory in addition to Beall's List.

Both take the same JSON the command line takes as a file, pasted directly:

```json
[{ "name": "IEEE", "domain": "ieee.org" }, "Nature"]
```

A plain string is matched by name; an object may carry `name` and/or `domain`.
The format is documented in full in
[user_lists/README.md](../src/bealls_list_check/user_lists/README.md).

The third control in Step 3 is the **Crossref** checkbox, on by default. Clearing
it forces a fully offline run — faster, and measurably less certain: records 4
and 8 of the example corpus fall back from `on_list` to `review` without it.

## 9 · Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| `.\setup.ps1` reports *"running scripts is disabled"* | PowerShell blocks unsigned scripts. Run `powershell -ExecutionPolicy Bypass -File .\setup.ps1`. |
| The launcher reports *"Virtual env not found"* | `run_ui` uses the `.venv` that `setup` creates. Run the setup script first. |
| The browser shows nothing at `localhost:8501` | The port was taken and Streamlit chose another. Use the URL the launcher printed. |
| **▶ Run check** stays greyed out | No papers are loaded. Complete Step 2; the button enables once the app confirms the count. |
| An upload is rejected as invalid JSON | The file must be a JSON list or a single object. `.jsonl` — one object per line — is not accepted here. |
| The thorough check warns about configuration | The named `.env` values are empty. Fill them in and **restart the app**; `.env` is read once, at startup. |
| A run appears to hang | There is no cancel and no timeout, and the deterministic pass reports only after each corpus file completes. A large corpus with Crossref on is slow, not stuck. |
| The report is written to a timestamped file | The default workbook was locked, usually because it is open in Excel. The run saves to `bealls_list_results_YYYYMMDD_HHMMSS.xlsx` instead, so nothing is lost. |
| A result belongs to a different check than the one selected | Results survive a change of selection and stay labelled with the check that produced it. Run again to refresh it. |
| Two runs return each other's workbook | Results go to one shared folder under a fixed name. Run one check at a time — one tab, and not alongside the command line. |
| The status preview has no colour | The coloured table needs pandas, which arrives as a Streamlit dependency. The table still renders without it. |

## Where to go next

- [ui/README.md](README.md) — the reference: every control, both downloads, and
  the table of differences between the app and the command line.
- [README.md](../README.md) — the project: verdicts, the workbook, the snapshot,
  and the limitations that govern responsible use.
- [examples/README.md](../examples/README.md) — the example corpus and the rules
  each of its records demonstrates.
- [match/README.md](../src/bealls_list_check/match/README.md) and
  [resolve/README.md](../src/bealls_list_check/resolve/README.md) — how a verdict
  is actually reached.
