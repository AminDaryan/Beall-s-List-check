# Kscien's list — fetching a local snapshot

> Deep-dive companion to [`fetch_kscien.py`](fetch_kscien.py). Sibling of
> [`scrape_bealls_list/`](../scrape_bealls_list/README.md), which does the same job
> for Beall's List.

The [Kscien Organization](https://kscien.org) publishes five lists of venues it
does not recommend. This module turns them into the same entry shape the Beall's
snapshot uses, so the matcher treats both catalogues identically and a match from
either is reported in the same words.

```bash
python src/bealls_list_check/fetch_kscien/fetch_kscien.py            # sync
python src/bealls_list_check/fetch_kscien/fetch_kscien.py --check    # report only
python src/bealls_list_check/fetch_kscien/fetch_kscien.py --full     # force a full pull
```

## The snapshot is not redistributed

Kscien's site carries *"Copyright © 2026 Kscien – All Rights Reserved"* and
publishes **no licence, terms or reuse page** — every one of `/terms/`,
`/license/`, `/copyright/` and `/privacy/` returns 404. Redistributing a copy is
therefore not ours to do.

Consequently `data/kscien_snapshot.json` is **git-ignored** and each user fetches
their own. `config.PROVIDERS["kscien"]["vendored"]` is `False`, and a run that
enables the list without a local copy stops with the command to fetch one rather
than silently checking against an empty catalogue.

> [!IMPORTANT]
> This is the one substantive difference from Beall's List, which *is* vendored.
> A Kscien-enabled run is reproducible only for whoever holds that dated
> snapshot; the Summary sheet records its date so a report can state it.

## What the lists contain

Verified against the live taxonomy on **2026-08-17**. Mapped onto the categories
the matcher already knows — a catalogue must map onto these rather than invent new
ones, because the weak-list downgrade and the fuzzy tier are keyed on them.

| Kscien list | slug | category | entries kept |
|---|---|---|---|
| Standalone Journals | `standalone-journals` | `standalone_journal` | 1,467 |
| Non-Recommended Publishers | `publishers` | `publishers` | 1,266 |
| Non-Recommended Conferences | `predatory-conferences` | `predatory_conference` | 454 |
| Hijacked Journals | `hijacked-journals` | `hijacked` | 233 |
| Misleading Metrics | `misleading-metrics` | `misleading_metric` | 53 |

**`predatory_conference` is Kscien's alone** — Beall's has no conference list — and
it is a *weak* category: a match is surfaced for a human, never asserted as a
confident hit. A paper is published in a journal rather than a conference series,
and conference names are short and generic.

Two slugs do **not** follow their display names (`publishers`, not
`non-recommended-publishers`; `predatory-conferences`, not
`non-recommended-conferences`). Guessing them from the display names dropped half
the catalogue on the first run and the output looked healthy, which is why an
unmapped list now prints a loud warning naming it.

## Access route

Kscien runs WordPress, which exposes its content over the standard REST API. This
is **not a service Kscien advertises or documents**, so treat it as an
implementation detail: every request degrades to a clear error rather than a
partial snapshot.

```
GET /wp-json/wp/v2/publishing-taxonomy?per_page=100    the five lists + counts
GET /wp-json/wp/v2/predatory-publishing?per_page=100   the entries, paginated
```

`per_page` is capped at **100 by WordPress itself** (101 returns
`rest_invalid_param`), so a first pull of ~3,539 entries is ~36 requests.

## Staying current without re-fetching everything

A full pull is needed once. After that a run costs **one request**:

| Question | How |
|---|---|
| Is there anything new? | `?per_page=1&orderby=modified&order=desc` — the newest change, in one request |
| What changed? | `?modified_after=<snapshot date>` — only those entries |
| How many are there? | the `X-WP-Total` response header |

That matters because Kscien describes the list as *"designed to be updated
daily"* while having modified **nine entries in the last twenty months** (the most
recent on 2026-08-15). Paying 36 requests per run for that would be waste.

Three cases still force a full pull, because an incremental fetch cannot see them:

- **Deletions.** `modified_after` cannot report a removed entry, so a live
  `X-WP-Total` *lower* than the snapshot's triggers a full pull.
- **A changed count** that is not explained by modifications.
- **A changed mapping.** If this module learns to map a list the local snapshot
  does not cover, nothing upstream changed and the freshness probe would say "up
  to date" while the snapshot silently omitted whole lists. The categories a
  snapshot covered are recorded in its metadata, and a snapshot that does not
  record them is treated as untrustworthy.

## What Kscien does not give you

- **No reasons.** Every one of the 3,539 records is a name plus a URL. There is no
  evidence field, no criteria, no assessment date, no assessor; the custom-fields
  object is empty on all of them. A match can therefore say *which* list an entry
  is on and *how* it matched — never *why Kscien listed it*. The report's wording
  is written to claim exactly that much and no more.
- **No ISSNs.** Zero of 3,539. So Kscien entries match on name and website only.
- **Little that Beall's does not already have.** Roughly 80% of the catalogue
  overlaps the vendored Beall snapshot by name or domain; the genuinely additive
  material is mostly the conference list.

## Data-quality handling

- **Mojibake.** Titles carry U+FFFD replacement characters from a mis-decoded
  import, non-breaking spaces, and HTML entities. `clean_title()` strips them —
  which matters for more than looks, because the entry's `normalized_name` is
  derived from the cleaned string, keeping
  `normalize_name(name) == normalized_name`. A later stage relies on that when it
  has only the display name to work from.
- **7 unassigned entries** belong to no list at all and are skipped and counted,
  not guessed at. Six of them are among the most recent additions.
- **Duplicates.** Deduplicated on `(list_source, normalized_name, domain)` — the
  same key the Beall's scraper uses — so two different venues that merely share a
  name are both kept. ~59 collapse.
- **11 entries have no website** and are kept: they can still match by name.
