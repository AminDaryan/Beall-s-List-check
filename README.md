# Beall's List predatory-venue check

A small, transparent tool that takes a batch of research papers and tells you
**which ones were published in a journal or by a publisher that appears on
[Beall's List](https://beallslist.net)** — the best-known (if unofficial and
contested) catalogue of *"predatory"* academic venues.

It is built for one job: screening a large reading list quickly. Checking 500
journals by hand takes hours; this tool does it in seconds and hands you a
colour-coded Excel report you can audit line by line.

> [!IMPORTANT]
> **A match means one specific thing: *"this venue appears on Beall's List."*
> It does NOT mean the paper is bad, wrong, or fraudulent.** The tool judges the
> **venue**, never the paper's content. Beall's List is unofficial, archived, and
> contested — some listed venues (e.g. *Frontiers*) are widely regarded as
> legitimate — so a match is an **allegation as of the snapshot date**, not proof.
> Uncertain matches are flagged for a human, never asserted.

---

## Table of contents

1. [What this tool does — and what it does *not* do](#1-what-this-tool-does--and-what-it-does-not-do)
2. [How it works (the pipeline and its components)](#2-how-it-works-the-pipeline-and-its-components)
3. [Key terms](#3-key-terms)
4. [Getting started (setup, web app, command line)](#4-getting-started)
5. [The input: Semantic Scholar paper records](#5-the-input-semantic-scholar-paper-records)
6. [How the check decides — and the six verdicts](#6-how-the-check-decides--and-the-six-verdicts)
7. [How to read the Excel report](#7-how-to-read-the-excel-report)
8. [Whitelist and blacklist (optional)](#8-whitelist-and-blacklist-optional)
9. [Data quality and the Crossref cross-check](#9-data-quality-and-the-crossref-cross-check)
10. [The thorough check (an LLM to resolve the `review` papers)](#10-the-thorough-check-an-llm-to-resolve-the-review-papers)
11. [Refreshing the Beall's List snapshot](#11-refreshing-the-bealls-list-snapshot)
12. [Project structure](#12-project-structure)
13. [Limitations and responsible use](#13-limitations-and-responsible-use)

> **Working in the code?** [ARCHITECTURE.md](ARCHITECTURE.md) maps every module and
> the data flow. Deep dives live next to their modules:
> [`match.md`](src/bealls_list_check/match.md) (the decision logic),
> [`resolve.md`](src/bealls_list_check/resolve.md) (DOI/Crossref resolution),
> [`user_lists.md`](src/bealls_list_check/user_lists.md) (whitelist/blacklist).

---

## 1. What this tool does — and what it does *not* do

**The problem.** Some academic journals are *predatory*: they charge authors a
publication fee but provide little or no genuine peer review. A widely-cited
attempt to catalogue such venues is **Beall's List**. If you are compiling a
literature survey, you may want to know — fast — whether any of your sources come
from a venue on that list, so you can give those a closer look.

**What the tool does.** For each paper you give it, it looks **only at the
publication venue** (the journal or publisher), normalises it, and compares it
against a local copy of Beall's List. It labels each paper with one of six
verdicts (see [§6](#6-how-the-check-decides--and-the-six-verdicts)) and writes
everything to an Excel workbook with the evidence for every decision.

- The tool judges the **venue**, never the **paper's content or quality**.
- `on_list` means *"appears on the list,"* not *"is predatory."*
- `review` always needs a human — it exists precisely because the match is
  uncertain.

**No AI is required.** The default ("fast") check uses **no LLM at all** — every
verdict is produced by deterministic rules and is fully traceable from the
report. An *optional* "thorough" mode adds an LLM opinion to help resolve the
genuinely uncertain cases (see [§10](#10-the-thorough-check-an-llm-to-resolve-the-review-papers)).

---

## 2. How it works (the pipeline and its components)

A run is a short, linear **pipeline**. Each stage is one focused module you can
read on its own; the arrow between them is a single *result dictionary* per paper
that each stage fills in.

```
load snapshot ─▶ match venue ─▶ resolve 'review' via DOI ─▶ check data quality ─▶ render Excel
```

| Stage | What it does | Module(s) | Deep dive |
|---|---|---|---|
| **1. Load** | Read the vendored Beall's List snapshot into a fast lookup index. | `config.py`, `match.py` | — |
| **2. Match** | Classify each paper's venue — website / ISSN / name / fuzzy, strongest signal first. | `match.py` | [`match.md`](src/bealls_list_check/match.md) |
| **3. Resolve** | Settle the uncertain `review` verdicts using the paper's DOI via Crossref (on by default; offline fallback). | `resolve.py`, `data_quality.py` | [`resolve.md`](src/bealls_list_check/resolve.md) |
| **4. Validate** | Flag records whose Semantic Scholar metadata looks unreliable (missing/merged venue, bad DOI/ISSN, …). | `data_quality.py`, `common/input_quality.py` | [§9](#9-data-quality-and-the-crossref-cross-check) |
| **5. Render** | Write a colour-coded Excel workbook with the evidence for every decision. | `report.py` | [§7](#7-how-to-read-the-excel-report) |

**Two front doors:** the **web app** (`ui/`) and the **command line**
(`bealls_list_check.py`) run the *same* code. An **optional LLM pass**
(`bealls_llm_check.py`) adds a recommendation to each `review` paper without ever
changing a deterministic verdict.

The **conductor** (`bealls_list_check.py`) wires these stages together; see
[ARCHITECTURE.md](ARCHITECTURE.md) for the full map.

---

## 3. Key terms

| Term | Plain-language meaning |
|---|---|
| **Predatory journal/publisher** | A venue that takes authors' money but does not provide the real peer review a legitimate journal would. |
| **Beall's List** | A catalogue of *alleged* predatory publishers and journals, originally by librarian Jeffrey Beall, now hosted unofficially at beallslist.net. It is **five lists**: predatory *publishers*, standalone *journals*, *hijacked* (cloned) journals, *fake-metrics* companies, and *vanity presses*. |
| **Venue** | The journal, publisher, or conference a paper was published in. This tool checks the venue, not the paper. |
| **`publicationVenue`** | The part of a Semantic Scholar record describing the venue: name, website, ISSN, etc. The most important field for the check. |
| **DOI** | *Digital Object Identifier* — a permanent ID for a published article, e.g. `10.1234/x`. |
| **ISSN** | *International Standard Serial Number* — an 8-digit ID for a journal, `NNNN-NNNX`. |
| **Preprint** | A paper posted to arXiv, bioRxiv, etc. before/instead of journal publication. It has no journal publisher, so it can't be "on Beall's List." |
| **Snapshot** | The local, dated copy of Beall's List this tool matches against. Stored in the repo so runs are reproducible and work offline. |
| **Crossref** | The official registry publishers deposit articles into ([crossref.org](https://www.crossref.org/)). Queried **by DOI** (as an API — it never opens the DOI link or the journal's page) to double-check metadata and resolve uncertain matches. On by default, with an offline fallback. |

---

## 4. Getting started

### 4.1 First-time setup (run once)

```powershell
.\setup.ps1           # Windows PowerShell
```
```bash
bash ./setup.sh       # macOS / Linux
```

This creates a private Python environment in `.venv`, installs
[`requirements.txt`](requirements.txt), and copies [`.env.example`](.env.example)
to `.env`. The web-app launchers use that environment automatically.

> [!NOTE]
> **You only need API keys for the "thorough" check.** The fast check, the
> data-quality checks, and Crossref cross-checking need **no keys at all**
> (Crossref is free). For the thorough check, set your LLM provider and key in
> `.env` (see the comments in [`.env.example`](.env.example)).

### 4.2 The web app (easiest)

```powershell
.\run_ui.ps1          # Windows PowerShell
```
```bash
bash ./run_ui.sh      # macOS / Linux
```

This opens `http://localhost:8501`, where you **choose** the fast or thorough
check, **provide** your papers (paste JSON or upload a `.json` file), press
**Run**, then **preview and download** the Excel report. An *Input data quality*
panel flags unreliable records as soon as your papers load.

### 4.3 The command line

To use the CLI scripts, activate the environment first:

```powershell
.\.venv\Scripts\Activate.ps1     # Windows PowerShell
```
```bash
source .venv/bin/activate         # macOS / Linux
```

```bash
# The FAST check (rules only, no LLM) — writes the Excel report. Uses Crossref by
# default when online, and falls back to fully offline with no internet. Add
# '--crossref off' to force offline.
python src/bealls_list_check/bealls_list_check.py

# The THOROUGH check — fast check + an LLM recommendation per 'review' paper.
python src/bealls_list_check/bealls_llm_check.py
python src/bealls_list_check/bealls_llm_check.py --limit 50   # cheap test
```

**Telling the check where your papers are.** By default it reads its corpus from
`docs/Updated Abstract Papers/*.json` relative to the project root. That folder is
*not* part of this repository, so on a fresh clone point the check at your own
folder of Semantic Scholar JSON files:

```powershell
$env:BEALLS_CORPUS_DIR = "C:\path\to\my\papers"   # Windows PowerShell
```
```bash
export BEALLS_CORPUS_DIR=/path/to/my/papers        # macOS / Linux
```

The check globs that folder for `*.json`, so **only** corpus files should live
there. (The web app handles this for you.)

---

## 5. The input: Semantic Scholar paper records

The input is **one JSON list of paper records, in exactly the shape
[Semantic Scholar](https://www.semanticscholar.org/) returns them.** Each record
describes a paper and, crucially, the venue it was published in. A minimal record:

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
    "alternate_urls": [],
    "alternate_names": []
  },
  "openAccessPdf": { "url": "" }
}
```

The more fields you provide, the more signals the check has:

| Field | Role in the check |
|---|---|
| `publicationVenue.url` | **The single strongest signal** — the venue's own website. |
| `publicationVenue.alternate_urls` | Other websites S2 associates with the venue. Trusted **less** (a match here is *review* only). |
| `publicationVenue.issn` | An exact ISSN match is a high-confidence hit. |
| `venue`, `publicationVenue.name`, `alternate_names` | All the **names** the venue is known by — for exact and fuzzy name matching. |
| `publicationVenue.type` | `journal` / `conference` / etc. — shown for context. |
| `externalIds.DOI` | Builds the clickable link, and is the key for the Crossref cross-check (on by default). |
| `openAccessPdf.url` | The host of the free PDF — a *weak* signal (earns a *review*, never an automatic hit). |
| `title`, `year`, `paperId` | Shown in the report; `paperId` also de-duplicates papers across files. |

---

## 6. How the check decides — and the six verdicts

Everything here is **deterministic**: the same input always gives the same
verdict, and the report shows you *why*.

> **The guiding principle.** Signals are tried **strongest-first, and the first
> one that matches wins.** A *website* or *ISSN* match is solid evidence; a *name*
> match is weaker; a *fuzzy* name match is weakest. Nothing uncertain is asserted
> — it is downgraded to `review` for a human.

In short, for each paper the check:

1. sets aside **preprints** and records with no venue → `no_venue`;
2. asserts `on_list` on a **strong** signal — the venue's own **website**, exact
   **ISSN**, or an **exact name** (≥ 2 words) backed by the paper's own website;
3. flags `review` on a **soft** signal — a name with no website backing, an
   **alternate** URL, a **free-PDF** host, or a **≥ 93% fuzzy** name;
4. otherwise → `clean`.

Three "strong" matches are still downgraded to `review` — **weak lists**
(vanity/fake-metrics), **hijacked-by-name** matches, and **exact names the
website doesn't back up** — and the soft `review` cases are then settled by the
paper's **DOI** ([§9](#9-data-quality-and-the-crossref-cross-check)).

📖 **The full rules** — every tier, the downgrades, the false-positive safeguards,
and the flow diagram — are in **[`match.md`](src/bealls_list_check/match.md)**.

### The six verdicts

Every paper gets exactly one **status**, and rows in the report are colour-coded
by it:

| Status | Colour | Meaning |
|---|---|---|
| `on_list` | 🔴 red | **Strong match:** venue website, exact ISSN, or exact name on a *core* Beall list. Read as *"appears on Beall's List."* |
| `review` | 🟠 orange | **Weak / uncertain match** a human should double-check. Not a confident accusation. |
| `clean` | 🟢 green | The venue was identified and is **not** on the list. |
| `no_venue` | ⚪ grey | A preprint or a record with no venue — nothing to check. |
| `out_of_scope` | 🔵 blue | *Only with a whitelist:* the venue wasn't in it, so it was skipped. |
| `error` | ⚫ dark | The record couldn't be processed (the reason is shown in the row). |

---

## 7. How to read the Excel report

The fast check writes
[`src/bealls_list_check/results/bealls_list_results.xlsx`](src/bealls_list_check/results/).
The two sheets you'll use most are **Results** (every paper) and **Flagged only**
(just the `on_list` + `review` papers). Each paper is a **block of rows**:

| Column | What's in it |
|---|---|
| **#** / **Status** | Paper number and the colour-coded verdict (span the block). |
| **Paper specifications** | Title / Year / Source file / DOI (a clickable link), plus the **Semantic Scholar venue** (name / type / website / ISSN) and **Mentioned in** (each linked venue tied to the paper). |
| **Match** | *Why* it was flagged: **Listed as** (the Beall entry, a link), **Beall list** (which of the five), and **Why flagged** (one plain sentence). |

Other sheets: **Data quality** (records that look unreliable, each with the
issue — [§9](#9-data-quality-and-the-crossref-cross-check)), **Summary** (totals +
run metadata), and **Legend** (a plain-language dictionary of every column). The
thorough check adds an **LLM review** column to the *Flagged only* sheet.

### A worked example

A plant-ecology paper's venue is reported as `Phyton`. The name matches a
**hijacked/cloned** entry — a predatory site (`engineeronline.net`) once
impersonated a real journal *Phyton: Annales Rei Botanicae* (authentic:
`verlag-berger.at`). A name match alone can't tell the genuine journal from the
counterfeit, so this starts as a doubtful `review`. The check then reads the
paper's DOI (`10.32604/phyton.2024.050556`) in Crossref: it was published by
**Tech Science Press** — *not* the counterfeit, but also *not* the recorded
authentic journal. Because it can't *prove* this is the genuine journal, it
**stays `review`** for a human. It clears a hijacked-by-name paper only when the
venue or DOI points to the authentic journal's own site — positive proof.
(See [`resolve.md`](src/bealls_list_check/resolve.md).)

---

## 8. Whitelist and blacklist (optional)

Two optional inputs (CLI flags, also in the web app), both a JSON list of
`{"name": ..., "domain": ...}` entries (a bare name string works too):

```bash
python src/bealls_list_check/bealls_list_check.py \
    --whitelist whitelist.json --blacklist blacklist.json
```

- **`--whitelist` narrows the scope** — only papers whose venue matches a
  whitelisted entry are checked; the rest are `out_of_scope`.
- **`--blacklist` extends the list** — its entries are matched like real Beall
  entries, so a paper in one comes back `on_list`.

📖 **The exact file format, field table, and matching semantics** are in
**[`user_lists.md`](src/bealls_list_check/user_lists.md)**.

---

## 9. Data quality and the Crossref cross-check

A verdict from bad input is worse than none, so the report includes a separate
**Data quality** sheet listing only the papers whose Semantic Scholar record
looks suspect, each with the specific issue: no venue at all, a malformed
DOI/ISSN, leftover HTML codes in the name, or a **"merged venue"** (one record
pointing at two different publishers' sites — the classic sign S2 fused two
same-named journals).

**Crossref (on by default).** Whenever there's internet, the check looks each
paper's DOI up in **Crossref** — the official publisher registry — to (a) name
the real journal for a merged venue, (b) flag any venue-name/ISSN disagreement,
and (c) resolve the uncertain `review` verdicts. It **falls back to a fully
offline run when there's no internet**, so you never have to ask for it. No key
is needed.

```bash
# Default: 'all' (every DOI) when online, offline when not — no flag needed.
python src/bealls_list_check/bealls_list_check.py

# Narrow it, or force offline:
python src/bealls_list_check/bealls_list_check.py --crossref flagged   # on_list/review DOIs only
python src/bealls_list_check/bealls_list_check.py --crossref off       # never contact Crossref
```

📖 **How DOI resolution works** — the four resolvers, the positive-proof rule, and
the reliability characteristics — is in
**[`resolve.md`](src/bealls_list_check/resolve.md)**.

> [!NOTE]
> Appearing on the Data quality sheet means *"double-check this by hand"* — **not**
> that the verdict is wrong. An empty result is **not** a guarantee the record is
> right; Semantic Scholar can be wrong in ways no automated check sees.

---

## 10. The thorough check (an LLM to resolve the `review` papers)

`review` papers are by definition uncertain, and going through them by hand is
slow. The thorough check asks an LLM for a concrete recommendation on **each
`review` paper — and only those:**

```bash
python src/bealls_list_check/bealls_llm_check.py
python src/bealls_list_check/bealls_llm_check.py --limit 50   # cheap test
```

For each *distinct* `review` venue (papers sharing a venue are grouped into one
call), it gives the model the nearest Beall's List entries, Beall's criteria, and
the venue's details, and writes the answer into an **"LLM review"** column on the
**Flagged only** sheet: *Predatory — recommend EXCLUDE* / *Legitimate — recommend
KEEP* / *Uncertain — check by hand*, plus a one-line reason.

It costs tokens (only for the small `review` backlog), **never** overrides the
mechanical verdict, and **never** declares a paper `on_list` on its own. Output
goes to `results/bealls_llm_results.xlsx`. It uses whatever `LLM_PROVIDER` /
`AZURE_OPENAI_DEPLOYMENT` your `.env` configures (Azure OpenAI, OpenAI, Anthropic,
Mistral, Gemini, or Ollama).

---

## 11. Refreshing the Beall's List snapshot

The tool matches against a **vendored snapshot** — a dated, structured copy of the
five Beall's List pages at
[`src/bealls_list_check/data/bealls_snapshot.json`](src/bealls_list_check/data/bealls_snapshot.json),
committed to the repo. The bundled snapshot holds **~3,045 entries**:

| List | Entries | What it is |
|---|---|---|
| Predatory **publishers** | 1,335 | Core list — predatory publishers. |
| Standalone **journals** | 1,501 | Core list — single predatory journals. |
| **Hijacked** journals | 139 | Core list — clones impersonating a real journal. |
| **Fake-metrics** companies | 55 | Weak — sellers of invented impact factors. |
| **Vanity** presses | 15 | Weak — book/monograph publishers. |

Vendoring (rather than scraping live each run) buys **reproducibility** (today's
run and next month's compare against the same list), **resilience** (the check
works offline; it doesn't depend on beallslist.net being up), and **honesty** (the
snapshot records *when* it was taken). To rebuild it from the live site:

```bash
python src/bealls_list_check/scrape_bealls_list.py
```

From the hijacked-journals table the scraper lists only the **predatory** column
as Beall entries; it also records that row's "authentic journal" as *reference
data* (used to clear false hijack matches — see
[`resolve.md`](src/bealls_list_check/resolve.md)), never listed as predatory
itself. It deliberately skips the site's *"Excluded — decide after reading"*
section (e.g. MDPI, which the author chose **not** to list).

---

## 12. Project structure

```
.
├── src/
│   ├── openai_client.py                # LiteLLM-backed client (one interface, many providers)
│   ├── common/
│   │   ├── input_quality.py            # shared input validation (the Data-quality checks)
│   │   ├── github_helpers.py           # URL helpers used by the validator
│   │   └── llm_helpers.py              # LLM call + JSON parsing + token counter
│   └── bealls_list_check/
│       ├── scrape_bealls_list.py       # Step 1: vendor data/bealls_snapshot.json
│       ├── bealls_list_check.py        # Step 2: the conductor — load → match → resolve → Excel
│       ├── bealls_llm_check.py         # Optional LLM pass over the 'review' papers
│       ├── match.py  / match.md        # Decide each paper's verdict  (+ deep-dive doc)
│       ├── resolve.py / resolve.md     # Upgrade/clear 'review' via the DOI  (+ deep-dive doc)
│       ├── data_quality.py             # S2 data-quality checks + the cached Crossref client
│       ├── user_lists.py / user_lists.md   # whitelist / blacklist handling  (+ deep-dive doc)
│       ├── descriptions.py             # All plain-English text shown in the workbook
│       ├── report.py                   # Render the Excel workbook (openpyxl)
│       ├── normalize.py                # Name / host / ISSN normalisation
│       ├── config.py                   # Paths + matching tunables
│       └── data/bealls_snapshot.json   # The vendored snapshot (committed)
├── ui/                                 # The Streamlit web app (app.py + runners.py)
├── ARCHITECTURE.md                     # Module map + data flow for contributors
├── requirements.txt
├── .env.example
├── setup.ps1 / setup.sh
└── run_ui.ps1 / run_ui.sh
```

---

## 13. Limitations and responsible use

- **Beall's List is unofficial, archived, and contested.** A match is an
  allegation as of the snapshot date — not proof a venue is predatory. Some
  listed venues (e.g. *Frontiers*) are widely considered legitimate.
- **This tool classifies the *venue*, not the *paper*.** It says nothing about a
  paper's correctness, quality, or the integrity of its authors.
- **`on_list` means "appears on the list," not "is predatory."** Treat it as a
  prompt to look closer, not a final judgement.
- **`review` always needs a human.** It exists precisely because the match is
  uncertain.
- **The fast check uses no LLM** and is fully deterministic and auditable from
  the *Match* column. The optional LLM pass only *adds* recommendations; it never
  changes a deterministic verdict.
- **Garbage in, garbage out.** Verdicts are only as good as Semantic Scholar's
  metadata — which is why the Data-quality sheet exists and Crossref
  cross-checking runs by default (whenever there's internet).
