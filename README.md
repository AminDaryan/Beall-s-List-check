# Beall's List predatory-venue check

A small, transparent tool that screens a batch of research papers and tells you
**which were published in a journal or by a publisher on
[Beall's List](https://beallslist.net)** — the best-known (if unofficial and
contested) catalogue of *"predatory"* academic venues. Checking 500 journals by
hand takes hours; this does it in seconds and hands you a colour-coded Excel
report you can audit line by line.

> [!IMPORTANT]
> **A match means one thing: *"this venue appears on Beall's List."*** It does NOT
> mean the paper is bad or fraudulent — the tool judges the **venue**, never the
> paper. Beall's List is unofficial, archived, and contested (some listed venues,
> e.g. *Frontiers*, are widely regarded as legitimate), so a match is an
> **allegation as of the snapshot date**, not proof. Uncertain matches are flagged
> for a human, never asserted.

> **Working in the code?** [ARCHITECTURE.md](ARCHITECTURE.md) maps every module and
> the data flow.

## Features

- **Deterministic venue matching** — website / ISSN / name / fuzzy, strongest
  signal first; no AI required. → [`match/`](src/bealls_list_check/match/README.md)
- **DOI resolution via Crossref** — settles the uncertain `review` verdicts and
  names the real journal for merged-venue records, using the paper's DOI. On by
  default; falls back to offline automatically. → [`resolve/`](src/bealls_list_check/resolve/README.md)
- **Whitelist / blacklist** — narrow the scope, or add your own predatory venues.
  → [`user_lists/`](src/bealls_list_check/user_lists/README.md)
- **Input data-quality checks** — flag unreliable Semantic Scholar records
  (missing/merged venue, malformed DOI/ISSN, encoding artifacts) on a dedicated
  sheet.
- **Colour-coded Excel report** — every decision carries its evidence; the
  workbook's **Legend** sheet documents every column and value.
- **Optional LLM pass** — a keep/exclude recommendation for each `review` paper
  (`bealls_llm_check.py`); it never overrides a deterministic verdict.
- **Batch CSV mode for server integration** — one command: a `.jsonl` of Semantic
  Scholar records in, a `SS-ID, DOI, Status` CSV out. The input and output
  structures are documented in full.
  → [`batch_check/`](src/bealls_list_check/batch_check/README.md)
- **Web app + CLI** — the same engine behind a Streamlit UI or the command line.

## How it works

A run is a short, linear pipeline; each stage is one module, passing a single
*result dictionary* per paper:

```
load snapshot ─▶ match venue ─▶ resolve 'review' via DOI ─▶ check data quality ─▶ render Excel
```

| Stage | Module(s) | Detail |
|---|---|---|
| Load the vendored Beall's List snapshot | `config.py`, `match/` | — |
| Match the venue (strongest signal first) | `match/` | [match/README.md](src/bealls_list_check/match/README.md) |
| Resolve `review` verdicts via the DOI (Crossref) | `resolve/`, `data_quality.py` | [resolve/README.md](src/bealls_list_check/resolve/README.md) |
| Flag unreliable input | `data_quality.py`, `common/input_quality.py` | — |
| Render the Excel workbook | `report.py` | — |

The conductor (`bealls_list_check.py`) wires the stages together; the web app
(`ui/`) and the CLI run the same code. See [ARCHITECTURE.md](ARCHITECTURE.md) for
the full map.

## Getting started

> [!IMPORTANT]
> **Run the one-time setup first** — `run_ui` uses the `.venv` it creates.

```powershell
.\setup.ps1        # Windows PowerShell   (macOS/Linux: bash ./setup.sh)
```

This creates a `.venv`, installs [`requirements.txt`](requirements.txt), and
copies [`.env.example`](.env.example) to `.env`. **No API keys are needed** for the
fast check, the data-quality checks, or Crossref — only the optional LLM pass
needs a key.

**Web app** (easiest):

```powershell
.\run_ui.ps1       # opens http://localhost:8501   (macOS/Linux: bash ./run_ui.sh)
```

Choose *fast* or *thorough*, paste or upload your papers (Semantic Scholar JSON),
press **Run**, then preview and download the report.

**Command line** (activate the env first — `.\.venv\Scripts\Activate.ps1` or
`source .venv/bin/activate`):

```bash
python src/bealls_list_check/bealls_list_check.py            # fast check
python src/bealls_list_check/bealls_llm_check.py --limit 50  # optional LLM pass (cheap test)

# batch mode: JSONL in, CSV (SS-ID, DOI, Status) out — for scripts/servers
python src/bealls_list_check/batch_check/batch_check.py papers.jsonl -o results.csv
```

The fast check uses **Crossref by default** when online and falls back to fully
offline automatically; add `--crossref off` to force offline, or `--crossref
flagged` to limit it to flagged papers.

**Your papers** live in `docs/Updated Abstract Papers/*.json` by default (not part
of this repo), or point the check elsewhere:

```bash
export BEALLS_CORPUS_DIR=/path/to/my/papers    # PowerShell: $env:BEALLS_CORPUS_DIR = "C:\path"
```

Input is a JSON list of Semantic Scholar records; the venue fields
(`publicationVenue.url` / `issn` / `name`, plus `externalIds.DOI`) drive the check
— see [`match/`](src/bealls_list_check/match/README.md) for exactly how each is used.

## The six verdicts

Every paper gets exactly one **status**, colour-coded in the report:

| Status | Colour | Meaning |
|---|---|---|
| `on_list` | 🔴 red | **Strong match:** venue website, exact ISSN, or exact name on a *core* Beall list. Read as *"appears on Beall's List."* |
| `review` | 🟠 orange | **Weak / uncertain match** a human should double-check. Not a confident accusation. |
| `clean` | 🟢 green | The venue was identified and is **not** on the list. |
| `no_venue` | ⚪ grey | A preprint or a record with no venue — nothing to check. |
| `out_of_scope` | 🔵 blue | *Only with a whitelist:* the venue wasn't in it, so it was skipped. |
| `error` | ⚫ dark | The record couldn't be processed (the reason is shown in the row). |

## Key terms

| Term | Plain-language meaning |
|---|---|
| **Predatory journal/publisher** | A venue that takes authors' money but does not provide real peer review. |
| **Beall's List** | A catalogue of *alleged* predatory venues, hosted unofficially at beallslist.net. It is **five lists**: predatory *publishers*, standalone *journals*, *hijacked* (cloned) journals, *fake-metrics* companies, and *vanity presses*. |
| **Venue** | The journal, publisher, or conference a paper was published in. This tool checks the venue, not the paper. |
| **DOI / ISSN** | Permanent identifiers for an article (`10.1234/x`) and a journal (`NNNN-NNNX`). |
| **Preprint** | A paper on arXiv, bioRxiv, etc. — no journal publisher, so it can't be "on Beall's List." |
| **Snapshot** | The local, dated copy of Beall's List the tool matches against — reproducible and offline. |
| **Crossref** | The official registry publishers deposit articles into. Queried **by DOI** (as an API — never opening the DOI link or the journal's page) to double-check metadata and resolve uncertain matches. On by default, with an offline fallback. |

## Refreshing the snapshot

The tool matches a **vendored snapshot**
([`data/bealls_snapshot.json`](src/bealls_list_check/data/bealls_snapshot.json),
**~3,045 entries** — 1,335 publishers, 1,501 journals, 139 hijacked, 55
fake-metrics, 15 vanity) so runs are reproducible and work offline. Rebuild it
from the live site with:

```bash
python src/bealls_list_check/scrape_bealls_list.py
```

## Limitations and responsible use

- **Beall's List is unofficial, archived, and contested.** A match is an
  allegation as of the snapshot date — not proof a venue is predatory.
- **This tool classifies the *venue*, not the *paper*.** It says nothing about a
  paper's correctness, quality, or its authors' integrity.
- **`on_list` means "appears on the list," not "is predatory."** Treat it as a
  prompt to look closer.
- **`review` always needs a human.** It exists precisely because the match is
  uncertain.
- **The fast check uses no LLM** and is fully deterministic and auditable from the
  *Match* column. The optional LLM pass only *adds* recommendations.
- **Garbage in, garbage out.** Verdicts are only as good as Semantic Scholar's
  metadata — which is why the Data-quality sheet exists and Crossref
  cross-checking runs by default.
