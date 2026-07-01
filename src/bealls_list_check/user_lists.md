# Whitelist and blacklist

> Companion to [`user_lists.py`](user_lists.py). The main
> [README](../../README.md#8-whitelist-and-blacklist-optional) gives the
> one-paragraph version and links here.

Two optional inputs let you tailor a run. Both are CLI flags and both are
available in the web app.

```bash
python bealls_list_check.py --whitelist whitelist.json --blacklist blacklist.json
```

- **`--whitelist` narrows the scope.** Only papers whose venue matches a
  whitelisted entry are checked; every other paper is marked `out_of_scope` and
  skipped. An empty or absent whitelist means "check everything" (the default).
- **`--blacklist` extends the list.** Its entries are added to Beall's List for
  this run (tagged `list_source="blacklist"`) and matched exactly like real Beall
  entries, so a paper in one comes back `on_list`.

## File format

Both files use the **same format**: a JSON list whose items are each either a
bare **name string** or an **object** with any of `name`, `domain` (or `url`),
and `issns`. At least one of `name` / `domain` is required; items with neither are
skipped. A single object (not wrapped in a list) is also accepted.

```json
[
  "Bentham Science Publishers",
  { "name": "Hindawi", "domain": "hindawi.com" },
  { "domain": "scirp.org" },
  { "name": "Journal of Example", "url": "https://example.com/jx", "issns": ["1234-5678"] }
]
```

| Field | Meaning |
|---|---|
| `name` | The venue or publisher name. In a **whitelist** it matches as a whole-word phrase (so `"IEEE"` covers *IEEE Transactions on …*, but `"nature"` won't match *signature*). In a **blacklist** it is matched like a real Beall name: an exact match needs ≥ 2 words and ≥ 5 letters, otherwise a ≥ 93% fuzzy match yields `review`. |
| `domain` / `url` | The venue's website — either key works (`url` wins if both are given). Matched exactly or as a subdomain, so a **publisher** domain covers all its journals (`ojs.x.com` matches a listed `x.com`). |
| `issns` | Optional list of ISSNs. Used by the **blacklist** (an ISSN match flags the paper `on_list`); **ignored** by the whitelist scope filter. |

## Whitelist semantics (scope filter)

A paper is *in scope* if **either**:

- any of its venue domains is the same as, or a subdomain of, a whitelisted
  domain; **or**
- a whitelisted name appears as a whole-word phrase in one of the paper's venue
  names.

Only the *venue* domains are considered (the canonical `publicationVenue.url`
plus `alternate_urls`) — never the open-access PDF host, since a paper's PDF
living on a publisher's server doesn't make it that publisher's journal.

## Blacklist semantics (extra predatory entries)

Blacklist entries become real snapshot entries in the `BeallIndex`, so they flow
through the exact same matching tiers as Beall's own lists (see
[`match.md`](match.md)): domain, ISSN, exact name (with the ≥ 2-word rule), and
fuzzy name. `blacklist` outranks every Beall list when several entries share a
domain — the user explicitly asserted it — and it is eligible for fuzzy near-name
matching.

## Web app

The two boxes under a checker accept the same JSON. The app writes your uploaded
papers to a temporary folder and passes the lists through as the same CLI flags,
so behaviour is identical to the command line.
