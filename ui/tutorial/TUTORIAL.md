# Tutorial — how to use the web app

> This guide goes through the app one step at a time.
>
> It starts with the eight example papers included in the project. Their correct
> result is already known, so a first run shows whether the installation works.
> Your own papers come after that. Every step says what should appear on screen,
> so a run can be checked instead of assumed.
>
> Every control is described in [ui/README.md](../README.md). The project as a whole
> is described in the main [README](../README.md).

## What this tool checks

For every paper, the app answers one question: **is the paper's journal or
publisher on [Beall's List](https://beallslist.net)?**

Beall's List names journals and publishers that may be *predatory* — they charge
a publication fee but skip the quality checks a real journal carries out.
Throughout this guide and in the report, the journal or publisher of a paper is
called its **venue**.

Three points are worth knowing before the first run:

- The app judges the **venue, not the paper**. A match means *this venue is on
  the list*. It does not mean the research is bad.
- Beall's List is unofficial and disputed, and the app uses a copy of it made on
  one fixed date. A match is therefore **a claim, not proof**. Matches that are
  not certain are marked for a person to look at, never stated as fact.
- The input is **Semantic Scholar paper records** — JSON data with a
  `publicationVenue` field. Links, titles and PDF files are not accepted.

<!-- app:skip-start -->
## Before you start

Installing the app and starting it are not steps of the guide. That is why these
two sections carry no number, and why the app's own Tutorial page leaves them
out: anyone reading this page inside the app has already done both.

### Install, once

You need **Python 3.9 or newer**, and an internet connection for the setup only.
In the project folder, run one of these:

```powershell
.\setup.ps1
```

```bash
bash ./setup.sh
```

The script creates `.venv`, installs everything the app needs, and copies
`.env.example` to `.env`. **No API keys are needed** for any part of this guide
except [the thorough check](#4-the-thorough-check-optional). Running the script
again is safe: an existing `.env` is never overwritten.

If PowerShell refuses to run the script and says *"running scripts is disabled on
this system"*, use this command instead:
`powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

### Start the app

```powershell
.\run_ui.ps1
```

```bash
bash ./run_ui.sh
```

The first time, this also installs Streamlit into `.venv`. It then prints a web
address, usually `http://localhost:8501`. If that port is already in use,
Streamlit takes the next free one — so open the address that was printed, not the
one written here.

The app runs **on one computer, for one person**: no server, no login, no
accounts. It runs the same scripts as the command line, so both produce the same
report. Pressing Ctrl+C in the terminal stops the app. Closing the browser tab
does not.
<!-- app:skip-end -->

## 1 · First run, with the example papers

The project includes eight example papers whose correct result is written down in
advance, so a new installation can be tested without any real data. Together they
produce every verdict.

![The app after startup, with the example papers loaded](../static/ui-main.png)

The sidebar holds two things: the links to the three pages (**Run a check**,
**Tutorial** and **Settings**) and one line on what the app does. Everything about the LLM is in
the flow itself, because only one of the three checks uses one. The app follows
the light or dark setting of the browser, and the **⚙** button in the top right
corner overrides it.

Every step has a header bar: a number, the title, an ⓘ that links to the matching
section of this guide, and a label on the right with that step's current answer.
The label is green when the step is finished, and orange when the step is what
the run is still waiting for. Reading the four labels from top to bottom shows
what still has to be decided.

**Step 1 · Choose a check.** The three checks are shown as a comparison table,
one per row, with a column for each thing they differ in: **Cost**, **Records**
and **Returns**. Hold the mouse over any of those labels for the detail behind it
— which file comes back, what a run costs, and whether repeated papers are
merged. Select **⚡ Fast check**: rules only, no API key, no cost. The selected
row is highlighted and its button becomes solid. Nothing else on the page
changes.

**Step 2 · Provide the papers.** There are two ways to supply your own papers:
*Upload a file* and *Paste JSON*. Below them is **📦 Load the example corpus**.
This is not a third way — it fills the paste box with the eight example papers,
so what is about to be checked is on screen and can be edited. Press it. The app
answers `✅ Loaded 8 paper(s) — ready to run`. Under that, a closed **Input data
quality** panel reads **1 of 8 record(s) flagged**. That is paper 6, which has no
venue information at all, on purpose. The panel inspects the data *before*
anything runs, without using the internet. It does not stop the run, and it can
be downloaded as `input_data_quality.xlsx`. Open it to see which paper was
flagged and why.

**Step 3 · Options.** Change nothing here. **Crossref** is on by default and
should stay on, and the two **venue list** boxes below it are empty, which is
what almost every run wants. (Choosing the thorough check adds an **LLM
provider** and a **Model / deployment** list here as well — see
[section 4](#4-the-thorough-check-optional).)

**Step 4 · Run.** A labelled summary repeats what is about to happen: which
check, how many papers, whether Crossref is on, which extra lists are used, and
what file comes back. Press **▶ Run check**. A progress bar and the newest log
line appear. The fast check does not count items one by one, so the bar moves
back and forth instead of filling up. Eight papers finish in a few seconds.

### What you should see

A green success message appears, together with a **download button** for
`bealls_list_results.xlsx` and a preview of the workbook's first sheet. The eight
papers come out as follows. These results were checked against the list copy made
on 2026-06-10; a newer copy may give a different answer for some venues.

| # | What this paper shows | Crossref on (default) | Crossref off |
|---|---|---|---|
| 1 | The venue's own website is a listed site. This is the strongest signal. | `on_list` | `on_list` |
| 2 | A well-known journal that is not on the list. | `clean` | `clean` |
| 3 | A preprint: there is no journal publisher to check. | `no_venue` | `no_venue` |
| 4 | The venue *name* matches, but the paper's own website does not confirm it. | `on_list` | `review` |
| 5 | A well-established journal whose DOI confirms the venue. | `clean` | `clean` |
| 6 | No venue information at all. | `no_venue` | `no_venue` |
| 7 | A match on the vanity-press list (publishers that print whatever an author pays for). That list is weak evidence, so the result is never certain. | `review` | `review` |
| 8 | A second web address points to a listed site; the main one does not. | `on_list` | `review` |

With Crossref on, the totals are 3 `on_list`, 1 `review`, 2 `clean` and 2
`no_venue`. Those numbers are the installation test: if they match, the whole
process works from end to end, Crossref included. Each paper is explained in full
in [examples/README.md](../../examples/README.md).

## 2 · Understanding the report

![A finished run, with the workbook preview](../static/ui-report.png)

The preview on screen shows the **first 40 rows of the first sheet**. The
workbook itself has five sheets, and the download button gives you the whole
file.

| Sheet | What is in it |
|---|---|
| **Results** | Every paper, in the order supplied, coloured by status, with the evidence behind each verdict. |
| **Flagged only** | Only the `on_list` and `review` papers, `on_list` first. The thorough check adds its column here. |
| **Data quality** | Papers whose data caused a warning. The sheet is missing when nothing was flagged, which means the data was fine, not that something went wrong. |
| **Summary** | How many papers per status, per kind of match and per list, plus details of the run. |
| **Legend** | An explanation of every column and every value, in plain words. |

Every paper gets exactly one **status**:

| Status | Colour | What it means |
|---|---|---|
| `on_list` | 🔴 red | Strong match: the venue's own website, an exact ISSN, or an exact name confirmed by the website, on one of the main lists. |
| `review` | 🟠 orange | Weak or unclear match. A person has to decide. This is not an accusation. |
| `clean` | 🟢 green | The venue was identified and is **not** on the list. |
| `no_venue` | ⚪ grey | A preprint, or a record with no venue. There is nothing to check. |
| `error` | ⚫ black | The paper could not be processed. The row says why. |

Two things about the workbook surprise most people the first time:

- **One paper takes four rows**, joined into a single block, below a header of
  two rows. The number of rows is therefore not the number of papers.
- **`clean` papers can appear on the *Data quality* sheet**, because the record
  of what Crossref answered is written there as well.

Below the result, a **Run log** section holds the text output of the check. It
opens by itself when a run fails.

## 3 · Checking your own papers

Your own papers go in as a JSON file of Semantic Scholar records. Such a file can
come from the [Semantic Scholar API](https://api.semanticscholar.org), or be
exported from a literature-search tool. In Step 2, choose **Upload a file** and drag the `.json`
file onto the box (or use its Browse button), or choose **Paste JSON** and paste
the text directly. Two shapes are accepted:

- a **JSON list** of paper objects, or
- a **single** paper object, which the app puts into a list by itself.

`.jsonl` files are **not** accepted in the app. Convert such a file into a JSON
list first, or use `batch_check.py` on the command line, which reads `.jsonl`,
`.ndjson` and `.json`. Streamlit accepts uploads of up to 200 MB per file.

The check only uses the fields that describe the venue. A record with the minimum
needed:

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

A paper that appears more than once is counted once. Repeats are recognised by
`paperId`, or by `title` when there is no `paperId`. A verdict can only be as
good as this data, which is why the **Input data quality** panel appears before
the run, and why the Crossref check is on by default.

A real run takes **minutes rather than seconds**. With Crossref on, the app
contacts the registry once for each different DOI. Setting `CROSSREF_MAILTO` in
`.env` moves those requests into Crossref's faster "polite pool".

## 4 · The thorough check (optional)

The thorough check first runs the fast check. It then asks an LLM (a large
language model, of the kind behind ChatGPT) for a **keep / exclude / uncertain**
recommendation on every venue with the status `review`. It never overrides a
verdict the rules produced. The recommendation appears as an **LLM review**
column on the *Flagged only* sheet of a second workbook,
`bealls_llm_results.xlsx`.

This is the only part of the app that needs an account with an LLM provider, and
the only part that costs money. Before using it:

1. Set `LLM_PROVIDER` in `.env` to `azure` (the default), `openai`, `anthropic`,
   `mistral`, `gemini`, or `ollama` (a model running on your own machine: no key,
   no cost).
2. Fill in the key for that provider, and put the model name in
   `AZURE_OPENAI_DEPLOYMENT` — for **every** provider, not only Azure. The name
   of that setting is historical.
3. Restart the app. `.env` is read once, at startup.

Those settings are the starting point, not the decision. Selecting the thorough
check adds two lists to Step 3 — **LLM provider** and **Model / deployment** —
and they, not `.env`, decide what answers a given run. Choosing a different
provider there changes that run only; the file is left alone, and a run started
from the command line is unaffected.

`AZURE_OPENAI_DEPLOYMENT` accepts one name, several names separated by commas, or
a JSON list, and every name in it appears in the model list. For a provider other
than the one `.env` selects, the names come from `LLM_MODELS_` followed by the
provider's name in capitals — `LLM_MODELS_ANTHROPIC`, for example — in the same
three shapes. A model that is in neither is still usable: choose **Other** at the
end of the list and type its name.

Step 4's summary and the line under Step 1 both name the provider and the model
that will actually be used, and the finished workbook records both on its
*Summary* sheet.

Selecting this check tests the configuration of whichever provider is selected
and names any key or web address that is missing, *before* any money is spent.
The test only confirms that the values are filled in. It does not confirm that
they are correct, and it says nothing about whether the model exists — a name
typed into **Other** is answered for by the provider, on the first request.

The cost depends on the number of **different venues with the status `review`**,
not on the number of papers: the rows are grouped by venue, and each group costs
one request. The app checks all of them; there is no limit setting in the
browser. For a small trial run, use the command line:
`python src/bealls_list_check/bealls_llm_check.py --limit 50`.

## 5 · Batch CSV, for scripts and servers

![Batch CSV mode, with colour-coded statuses](../static/ui-batch.png)

The third check returns three columns: `SS-ID`, `DOI` and `Status`. There is
**one row per supplied record, in the order supplied, and nothing is merged**.
The same table also comes back as an Excel file with colours, because a CSV file
cannot hold colour.

This check classifies every record, so it takes neither a whitelist nor a
blacklist. Step 3 therefore shows no **venue list** boxes for it, and Step 4's
summary reads `not used here` for both. The reason appears when the mouse is over
those words.

After a run that produced a workbook, a button **Also produce the batch CSV**
appears. It makes the same CSV file without going back to Step 1. It is a button
rather than something automatic because the CSV cannot be made from the workbook:
the workbook merges repeated papers, while the CSV keeps every record. Producing
it therefore means running the rule-based check — and its Crossref lookups — a
second time.

## 6 · Crossref, and your own venue lists

The first option in Step 3 is the **Crossref** checkbox, on by default. Crossref
is the official registry of published articles; the app looks each paper up there
by its DOI to confirm the venue. Switching it off forces a fully offline run:
faster, but the results are less certain. Example papers 4 and 8 fall back from
`on_list` to `review` without it.

Below it, under **Venue lists**, are two optional boxes, available on the fast
check and the thorough check. They are empty on almost every run, and being able
to see that they are empty is the point of their being on the page:

- **whitelist** — selects the papers: only those from the venues on it are
  checked, and only those appear in the report. A paper from any other venue is
  left out of the workbook entirely.
- **blacklist** — adds venues to the list, treating them as predatory for this
  run only.

Each box carries a **?** beside its name giving the same sentence in the app.

A paper left out by the whitelist was **not examined**. Its absence from the
report says nothing about it — not that it is fine, and not that it is not — so
a whitelist is for asking about a set of venues, never for clearing the rest.
The Summary sheet states how many papers were excluded, so a report of 50 rows
made from a corpus of 500 says so on its face.

Whitelisting a venue never hides a finding about it: a whitelisted venue that is
on Beall's List still comes back `on_list`.

### Adding a venue

Each list has **one box**. Type a name, a website or an ISSN into it; what you
type decides how the venue is matched, and the row that appears says which:

| What you type | Example | What it matches |
|---|---|---|
| An ISSN | `2666-2485` | That one journal, exactly. |
| A website | `ieee.org` | That site and everything under it — so a publisher's address covers all of its journals. |
| Anything else | `Journal of Widgets` | Any venue name containing those words. |

An ISSN is the most precise, so prefer it when you mean one particular journal.
A website or a name is the right choice when you mean a whole publisher.

Press **Add** — the small button at the right-hand end of the box — to add
exactly what is typed. It appears as soon as you type something. Typing also
searches a venue directory (see below), and choosing one of its matches adds that
venue instead.

Added venues appear in the box underneath. Remove one with the **✕** beside it.

Each row says **what the check will look for**, not just what the venue is
called:

> **IEEE Open Journal of Systems Engineering**
> MATCHES ON · `ISSN 2771-9987` · `venue name`

That row will match a paper whose ISSN is exactly `2771-9987`, and also any paper
whose venue name contains that title as a whole phrase. Hover a label to read
what it matches and how widely. The labels are worth a glance, because they
differ in how much they cover:

| Label | Matches | How wide |
|---|---|---|
| `ISSN 1234-5678` | papers with exactly that ISSN | one journal, and nothing else |
| `site example.com` | papers whose venue is on that host, or any host under it | every journal on that site |
| `venue name` | papers whose venue name contains this name as a whole word | depends on the name — a short or common one catches journals you did not mean |

A **⚠** on a site label means the address looks like a shared publisher platform,
so it covers every journal hosted there rather than just this one.

### Finding a venue in a directory

If you know the venue but not its ISSN, just start typing its name. Matches
appear underneath **as you type**, so `IEE` is already enough to see IEEE's
journals.

The two boxes search different directories, because they are for opposite
things:

- **Whitelist** — the [Directory of Open Access Journals](https://doaj.org),
  about 23,000 journals that passed its editorial screening.
- **Blacklist** — [Crossref](https://www.crossref.org), the registry publishers
  pay to register DOIs with. Nobody is screened to be in it, so it lists journals
  a directory of vetted ones never would — and a venue you want to mark as
  predatory will not be in a directory of vetted journals. Crossref starts
  searching once you have typed two letters.

**Choose one and it is added**, with its official title and ISSN, so you do not
have to look either up. There is no Add button beside each match; choosing is the
action. The line under the box says how many were found — `8 of 138`, say.

The menu shows the eight best matches. If there are more, its last row offers
them: **▾ Show 20 more** keeps what you typed and lengthens the list.

**If nothing matches, press Add and your text is added as typed.** So a venue
DOAJ has never heard of goes into the same box as one it has: type `WOl`, see
that nothing matched, press Add. The button sits inside the box on the right and
appears as soon as you type something.

The journal's website is *not* added — only its title and ISSN. That is
deliberate: many journals are published on a shared platform, so adding the
website of one Elsevier journal would put every Elsevier journal in scope. DOAJ
matches whose address looks like a shared platform are marked with a **⚠**;
Crossref does not record an address at all. If you do want a publisher's whole
site, type its domain into the box instead.

Out of the box both boxes ask their directory over the internet as you type, so
both need a connection — unless you have downloaded that directory on the
**Settings** page, in which case the box searches the copy on your own computer
and needs nothing. Nothing is downloaded unless you ask for it there. While a box
is asking over the internet, the **Add** button turns into a small spinner — the
answer takes about a tenth of a second from DOAJ and about half a second from
Crossref, and the spinner is how you know the box is still working rather than
finished with nothing to show.

> These searches only help you *name* a venue. Whether a journal is in DOAJ or
> Crossref has no effect on any verdict — the check asks whether the venue is on
> Beall's List, which is a different question.

### Working without internet — the Settings page

The **Settings** page decides **where** each directory is searched. It cannot
turn a search box off — both answers work, and the app starts on the first one:

- **Over the internet.** The box asks the directory's own search service. Nothing
  to download and nothing stored on your computer; it needs a connection, and
  DOAJ occasionally refuses a search when it is busy.
- **On this computer.** The whole journal list is downloaded once and searched
  where it sits. Instant, and it works with no connection at all.

| | DOAJ (whitelist) | Crossref (blacklist) |
|---|---|---|
| Over the internet | about a tenth of a second a search | about half a second a search, and it waits for a second letter before asking |
| Download, once | about 25 MB | about 51 MB |
| Takes | a few seconds | about 20 seconds |
| Kept on disk | about 4 MB | about 18 MB |
| On this computer | instant | instant, and from the first letter |

**Nothing is downloaded until you choose "On this computer"**, and choosing "Over
the internet" again keeps the file — so choosing the copy a second time is
instant rather than another download. A copy older than a fortnight is replaced
on its own, so there is nothing to refresh by hand. The line under each pair of
options reports what is actually on your computer: how many journals, how big,
and how long ago it arrived.

One difference worth knowing when building a list: DOAJ records a website for
every journal, and Crossref records none at all. So a venue added from the
blacklist's directory matches on its name and ISSN, never on a domain.

Nothing on that page changes any verdict. Both directories only help you *name* a
venue.

### Editing the list as JSON

**{ } Edit as JSON** shows the same list in the format the command line reads
from a file. Paste one in and it appears as rows; edit the rows and the JSON
follows.

```json
[{ "name": "IEEE", "domain": "ieee.org" }, { "issns": ["2666-2485"] }, "Nature"]
```

A plain text entry is matched by name. An object can carry a `name`, a `domain`,
`issns`, or any combination. The format is described in full in
[user_lists/README.md](../../src/bealls_list_check/user_lists/README.md).

## 7 · Common problems

| Problem | Cause and solution |
|---|---|
| `.\setup.ps1` says *"running scripts is disabled"* | PowerShell blocks scripts that are not signed. Run `powershell -ExecutionPolicy Bypass -File .\setup.ps1` instead. |
| The start script says *"Virtual env not found"* | `run_ui` uses the `.venv` folder that `setup` creates. Run the setup script first. |
| The browser shows nothing at `localhost:8501` | The port was already in use, so Streamlit took another one. Use the address the launcher printed. |
| **▶ Run check** stays grey | No papers are loaded. Finish Step 2; the button switches on once the app confirms how many papers there are. |
| An upload is refused as invalid JSON | The file has to be a JSON list or a single object. `.jsonl` — one object per line — is not accepted here. |
| The thorough check warns about the configuration | The `.env` values it names are empty. Fill them in and **restart the app**: `.env` is read once, at startup. |
| A run looks stuck | There is no cancel button and no time limit, and the rule-based check reports only after each input file is finished. Checking many papers with Crossref on is slow, not stuck. |
| The report file name ends in a date and time | The normal file was locked, usually because it is open in Excel. The run saves to `bealls_list_results_YYYYMMDD_HHMMSS.xlsx` instead, so nothing is lost. |
| The result belongs to a different check than the selected one | A result stays on screen when the selection changes, and keeps the name of the check that produced it. Run again to replace it. |
| Two runs return each other's file | Results go to one shared folder under fixed names. Run one check at a time — one browser tab, and not alongside the command line. |
| The status preview has no colour | The coloured table needs pandas, which is normally installed together with Streamlit. Without it the table still works, only without colour. |

## Where to go next

- [ui/README.md](../README.md) — the reference: every control, both downloads, and
  the table of differences between the app and the command line.
- [README.md](../README.md) — the project: the verdicts, the workbook, the list
  copy, and the limits to keep in mind when using the results.
- [examples/README.md](../../examples/README.md) — the example papers, and the rule
  each one demonstrates.
- [match/README.md](../../src/bealls_list_check/match/README.md) and
  [resolve/README.md](../../src/bealls_list_check/resolve/README.md) — how a verdict
  is actually reached.
