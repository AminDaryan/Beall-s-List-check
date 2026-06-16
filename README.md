# Beall's List predatory-venue check

## What is this?

Given a batch of research papers, this tool tells you **which ones were published
in a journal or by a publisher that appears on [Beall's List](https://beallslist.net)** —
a well-known (if unofficial and contested) catalogue of "predatory" venues
accused of charging fees while providing little or no real peer review.

For example: you're screening 500 papers for a survey and want to quickly spot
any that come from a questionable journal, so you can give those a closer look.
Checking 500 journals by hand would take a long time; this tool does it in
seconds and hands you a colour-coded Excel report.

**Important:** a match means *"this journal appears on the list."* It does **not**
mean the paper is bad. Beall's List is frozen (around 2021) and some listed
publishers are widely considered legitimate. The tool looks only at the
**venue**, never at the paper's content, and the uncertain matches are flagged
for a human to check rather than asserted.

The fast check uses **no LLM** — every verdict is mechanical and traceable.
There is an optional "thorough" mode that uses an LLM to help you resolve the
uncertain cases.

---

## The fastest way to try it: the web app

```powershell
.\run_ui.ps1          # Windows PowerShell
```
```bash
bash ./run_ui.sh      # macOS / Linux
```

This opens a page in your browser (`http://localhost:8501`). You: **(1)** pick the
fast or thorough check, **(2)** paste or upload your papers as JSON, **(3)** press
**Run**, then preview and download the Excel. As soon as your papers load, an
**Input data quality** panel flags anything about the records that looks
unreliable. The web app runs the same code as the command line, so the output is
identical.

---

## What you provide as input

Records exported from [Semantic Scholar](https://www.semanticscholar.org/) — one
JSON list of paper records, where each record describes a paper and, crucially,
the **journal it was published in** (the `publicationVenue` field). A minimal
record looks like:

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
  },
  "openAccessPdf": { "url": "" }
}
```

You can paste/upload this in the web app, or place one or more such JSON files in
a folder and point the check at it (see below).

---

## First-time setup

```powershell
./setup.ps1           # Windows PowerShell
```
```bash
bash ./setup.sh       # macOS / Linux
```

This creates a private Python environment in `.venv`, installs the libraries from
[`requirements.txt`](requirements.txt), and copies [`.env.example`](.env.example)
to `.env`. The web-app launchers (`run_ui.*`) use that environment automatically.
To run the command-line scripts yourself, activate it first:

```powershell
.\.venv\Scripts\Activate.ps1     # Windows PowerShell
```
```bash
source .venv/bin/activate         # macOS / Linux
```

> **You only need API keys for the "thorough" check.** The fast check, the
> data-quality checks, and the optional Crossref cross-check need no keys at all.
> For the thorough check, set your LLM provider and key in `.env` (see the
> comments in [`.env.example`](.env.example)).

---

## Running from the command line

```bash
# Step 1 (optional): refresh the local copy of Beall's List. A snapshot is
#         already included, so you can normally skip this.
python src/bealls_list_check/scrape_bealls_list.py

# Step 2: the FAST check (rules only, no LLM) — writes the Excel report.
python src/bealls_list_check/bealls_list_check.py

# Optional: the THOROUGH check — fast check + an LLM recommendation for each
#           'review' paper (costs tokens).
python src/bealls_list_check/bealls_llm_check.py
python src/bealls_list_check/bealls_llm_check.py --limit 50   # cheap test
```

By default the corpus is read from `docs/Updated Abstract Papers/*.json`; point it
elsewhere with the `BEALLS_CORPUS_DIR` environment variable (the web app handles
this for you).

---

## What the verdicts mean

Every paper gets one of these **statuses** (rows are colour-coded by it):

| Status | Meaning |
|---|---|
| `on_list` | A **strong** match: the journal's own website, its exact ISSN, or its exact name is on the list. Read as "appears on Beall's List." |
| `review` | A **weak or uncertain** match a human should double-check (the name is only *similar* to a listed one, or only a secondary web link points to a listed site, etc.). Not a confident accusation. |
| `clean` | The journal was identified and is **not** on the list. |
| `no_venue` | A preprint (e.g. arXiv) or a record with no journal — nothing to check. |
| `out_of_scope` | Only with a *whitelist*: the paper's journal wasn't in it, so it was skipped. |
| `error` | The record couldn't be processed (the reason is shown in the row). |

---

## How to read the Excel report

The report (`src/bealls_list_check/results/bealls_list_results.xlsx`) has several
sheets. The two you'll use most are **Results** (every paper) and **Flagged only**
(just the `on_list` + `review` papers). Each paper is shown as a **block of rows**
so every detail gets its own line:

| Column | What's in it |
|---|---|
| **#** | The paper's number (spans the whole block). |
| **Status** | The verdict above, colour-coded (spans the whole block). |
| **Paper specifications** | A header grouping the next three columns — everything describing the paper itself: |
| &nbsp;&nbsp;• Paper | Title / Year / Source file / DOI (the DOI is a clickable link). |
| &nbsp;&nbsp;• Semantic Scholar venue | The journal as Semantic Scholar reports it: name / type / website / ISSN. |
| &nbsp;&nbsp;• Mentioned in | Every journal/publisher/venue **name** tied to the paper (names only, no links). This is the one column that can grow: a paper recorded under 10 names makes a 10-row block; the other columns leave the extra rows blank. |
| **Match** | *Why* it was flagged: **Listed as** (the matching Beall entry, a link), **Beall list** (which list it came from), and **Why flagged** (one plain sentence: how it matched and what that means). |

Other sheets: **Data quality** (only papers whose Semantic Scholar record looks
unreliable, each with the specific issue), **Summary** (totals), and **Legend** (a
full plain-language dictionary). The thorough check adds an **LLM review** column
to the Flagged-only sheet.

**A worked example.** Say a plant-ecology paper comes back as `review`:

- **Semantic Scholar venue → Venue:** `Phyton`
- **Mentioned in:** `Phyton`, `Annales Rei Botanicae`
- **Match → Listed as:** `Phyton` (a *hijacked / cloned journal* entry)
- **Match → Why flagged:** *"the name matches a hijacked-journal entry — but
  hijacked clones reuse the real journal's name, so this may be the legitimate
  journal; verify."*

So a predatory site once impersonated the real journal *Phyton*, which is why the
**name** is on the list — but your paper is very likely the genuine *Phyton* (a
long-standing Austrian botany journal). That's exactly why it's `review`, not
`on_list`: it needs a human's eyes, not an automatic verdict. The **thorough**
check's LLM review column would make that call for you ("Legitimate — keep").

---

## Whitelist and blacklist (optional)

Tailor a run with two optional inputs (CLI flags, also in the web app). Each is a
JSON list of `{"name": ..., "domain": ...}` entries (a plain string works as just
a name):

```bash
python src/bealls_list_check/bealls_list_check.py \
    --whitelist whitelist.json --blacklist blacklist.json
```

- **`--whitelist` narrows the scope.** Only papers whose journal matches a
  whitelisted entry are checked; the rest are marked `out_of_scope`.
- **`--blacklist` extends the list.** Its entries are added to Beall's List for
  this run and flagged like real entries, so a paper in one comes back `on_list`.

---

## Data quality (catching unreliable input)

Semantic Scholar's data is sometimes wrong — it occasionally merges two journals
that share a name, stores an out-of-date web address, or omits the journal. A
verdict from bad input is worse than none, so the report includes a separate
**Data quality** sheet listing only the papers whose record looks suspect, each
with the specific issue (no venue info, a malformed DOI/ISSN, a "merged venue"
that points at two publishers, …).

Optionally, cross-check against **Crossref** (the official registry publishers
submit their articles to): when enabled, the journal name and ISSN Semantic
Scholar reports are compared with Crossref's record and any disagreement is
flagged.

```bash
# 'flagged' = only the on_list/review papers (cheap); 'all' = every DOI.
python src/bealls_list_check/bealls_list_check.py --crossref flagged
```

> Appearing on the Data quality sheet means *"double-check this by hand"* — not
> that the verdict is wrong; and an empty result is not a guarantee it's right.

---

## The thorough check (an LLM to resolve the `review` papers)

The fast check is precise, but `review` papers are by definition uncertain, and
going through them by hand is slow. The thorough check asks an LLM for a concrete
recommendation on **each `review` paper** (and only those):

```bash
python src/bealls_list_check/bealls_llm_check.py
python src/bealls_list_check/bealls_llm_check.py --limit 50   # cheap test
```

For each distinct `review` journal it asks the LLM — given the nearest Beall's
List entries, Beall's predatory-journal criteria, and the model's own knowledge —
whether the journal or its publisher is predatory, and writes the answer into an
**"LLM review"** column on the **Flagged only** sheet:

- **Verdict:** *Predatory — recommend EXCLUDE* / *Legitimate — recommend KEEP* /
  *Uncertain — check by hand*
- **Reason:** one sentence.

It costs tokens (only for the small review backlog), **never** overrides the
mechanical verdict, and never declares a paper `on_list` on its own. Output goes
to `src/bealls_list_check/results/bealls_llm_results.xlsx`. It uses whatever
`LLM_PROVIDER` / `AZURE_OPENAI_DEPLOYMENT` your `.env` configures.

---

## Project structure

```
.
├── src/
│   ├── openai_client.py                # LiteLLM-backed client (one interface, many providers)
│   ├── common/
│   │   ├── input_quality.py            # shared input-validation (the Data quality checks)
│   │   ├── github_helpers.py           # URL helpers used by the validator
│   │   └── llm_helpers.py              # LLM call + JSON parsing + token counter
│   └── bealls_list_check/
│       ├── scrape_bealls_list.py       # Step 1: vendor data/bealls_snapshot.json
│       ├── bealls_list_check.py        # Step 2: match the corpus → Excel (no LLM)
│       ├── bealls_llm_check.py         # Optional LLM pass over the 'review' papers
│       ├── match.py                    # The matching logic (domain / ISSN / name / fuzzy)
│       ├── data_quality.py             # The Semantic-Scholar data-quality checks
│       ├── normalize.py                # Name / host / ISSN normalisation
│       ├── config.py                   # Paths + matching tunables
│       ├── user_lists.py               # whitelist / blacklist handling
│       └── data/bealls_snapshot.json   # The vendored snapshot (committed)
├── ui/                                 # The Streamlit web app (app.py + runners.py)
├── requirements.txt
├── .env.example
├── setup.ps1 / setup.sh
└── run_ui.ps1 / run_ui.sh
```
