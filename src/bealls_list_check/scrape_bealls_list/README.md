# Building the snapshot — the scraper

> Deep-dive companion to [`scrape_bealls_list.py`](scrape_bealls_list.py). The main
> [README](../../../README.md) links here from its "Refreshing the snapshot"
> section. For how the snapshot is *consumed*, see [`match/`](../match/README.md).

The check never scrapes [beallslist.net](https://beallslist.net) at run time. It
matches against a **vendored snapshot** —
[`data/bealls_snapshot.json`](../data/bealls_snapshot.json) — and this script is
the one thing that produces it. Run it only when you want to refresh that file:

```bash
python src/bealls_list_check/scrape_bealls_list/scrape_bealls_list.py
```

## Why vendor a snapshot instead of scraping live

- **Reproducibility** — a run today and a run next month compare against the
  *same* list, so any difference in results comes from the papers, not from the
  website changing under you.
- **Resilience** — the matcher works fully offline and doesn't depend on the site
  being up (it has gone down before).
- **Honesty** — the snapshot records *when* it was taken. Beall's List is
  frozen/contested and not authoritative ground truth, so the date matters: a
  match is an allegation *as of the scrape date*.

## What it scrapes

Beall's List is really **five lists**. The scraper fetches each list page and
tags every entry with its `list_source`, so a match can say which list it came
from (and so the weak lists can be treated differently — see [`match/`](../match/README.md)):

| Page | `list_source` | Parse mode |
|---|---|---|
| `/` | `publishers` | link list |
| `/standalone-journals` | `standalone_journal` | link list |
| `/hijacked-journals` | `hijacked` | HTML table |
| `/misleading-metrics` | `misleading_metric` | link list |
| `/vanity-press` | `vanity_press` | link list |

The informational pages on the site are skipped. `misleading_metric` (metric
vendors) and `vanity_press` (book publishers) are included for completeness but a
paper venue realistically only matches the first three; their `list_source` tag
lets the matcher surface them as *weak* signals rather than assert them.

## The parsing, and the traps it avoids

The site is hand-maintained HTML, so the scraper is deliberately defensive:

- **Link lists** (`parse_links`) capture two kinds of entry: `<li><a
  href>Name</a></li>` (the common case; only *external* links are kept, so the
  site's own nav/footer links are dropped) and bare `<li>Name</li>` name-only
  entries (a few publishers are listed as plain text — kept, but only from `<ul>`
  blocks that already hold real linked entries, so navigation lists can't leak
  in). Name-only entries have no domain and can match by name alone.
- **The "Excluded" section is removed first** (`strip_excluded_sections`). The
  Publishers page ends with *"Excluded — decide after reading"*, listing venues
  the author deliberately **did not** put on the list (famously MDPI). Scraping
  those as entries would be a false positive, so that heading's section is cut
  before parsing; the real "Original list" and "Update" sections above it are
  untouched.
- **The hijacked table takes only the predatory column.** Each row pairs a
  *counterfeit* site with the *authentic* journal it impersonates. Only the
  hijacker is recorded as a Beall entry; the authentic journal is kept separately
  (`authentic_url` / `authentic_domain`) so the DOI resolver can *clear* a
  hijacked-by-name review when a paper really belongs to the genuine journal —
  see [`resolve/`](../resolve/README.md).
- **HTML entities are decoded** (`_text`), so a listed *"Science &amp;
  Education"* becomes *"Science & Education"* and normalises to the same key as
  the venue string Semantic Scholar reports.
- **Transient failures retry** (`fetch`): the site intermittently returns 503
  under load, and one blip shouldn't silently yield an incomplete snapshot, so
  each page is retried with backoff before the scrape is allowed to fail.

## The entry schema, and normalisation

Every entry is normalised at scrape time (via [`normalize.py`](../normalize.py))
so the matcher can do O(1) exact lookups without re-normalising:

```python
{
  "name",            # the listed name, as shown on the site
  "url",             # the entry's own website ("" for name-only entries)
  "domain",          # normalised host of url ("" if none)
  "issns",           # any ISSNs parsed from the entry
  "normalized_name", # lower-cased, accent/punctuation-stripped name (the match key)
  "list_source",     # publishers | standalone_journal | hijacked | misleading_metric | vanity_press
  "authentic_url",   # hijacked only: the genuine journal's site
  "authentic_domain" # hijacked only: its normalised host
}
```

Entries are **deduplicated** by `(list_source, normalized_name, domain)` before
being written.

## The output file

The snapshot is a single JSON document with run metadata and the entries:

```json
{
  "meta": {
    "source": "https://beallslist.net",
    "scraped_utc": "2026-06-10T08:57:10+00:00",
    "pages": ["/", "/standalone-journals", "/hijacked-journals", "..."],
    "counts_raw": { "publishers": 1335, "standalone_journal": 1501, "...": 0 },
    "total_entries": 3045,
    "note": "Unofficial archived copy … an allegation as of the scrape date …"
  },
  "entries": [ /* the schema above, one per listed venue */ ]
}
```

It is written to [`../data/bealls_snapshot.json`](../data/bealls_snapshot.json),
which **is** checked into the repo (unlike the corpus and the results) precisely
so runs are reproducible and offline by default.
