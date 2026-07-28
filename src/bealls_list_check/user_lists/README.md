# Whitelist and blacklist

> Companion to [`user_lists.py`](user_lists.py). The main
> [README](../../../README.md) gives the
> one-paragraph version and links here.

Two optional inputs let you tailor a run. Both are CLI flags, and both are
available in the web app's **Fast** and **Thorough** checks (batch CSV mode has
neither — it classifies every record).

```bash
python src/bealls_list_check/bealls_list_check.py --whitelist whitelist.json --blacklist blacklist.json
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
and `issns`. At least one of the three is required; items carrying none of them
are skipped. A single object (not wrapped in a list) is also accepted.

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
| `issns` | Optional list of ISSNs. Matched exactly by **both** lists — an ISSN hit flags a paper `on_list` from a blacklist, and puts it in scope from a whitelist. The most precise of the three: an ISSN names one journal, while a name can be shared and a domain can be a whole publisher. An entry may carry nothing else. |

## Whitelist semantics (scope filter)

A paper is *in scope* if **any** of the following matches:

- one of its venue ISSNs is whitelisted;
- any of its venue domains is the same as, or a subdomain of, a whitelisted
  domain;
- a whitelisted name appears as a whole-word phrase in one of the paper's venue
  names.

Only the *venue* domains are considered (the canonical `publicationVenue.url`
plus `alternate_urls`) — never the open-access PDF host, since a paper's PDF
living on a publisher's server doesn't make it that publisher's journal. ISSNs
come from `publicationVenue.issn`, parsed by the same helper the Beall matcher
uses, so both sides of the comparison are keyed identically.

### Which identifier to whitelist

The three are not equally precise, and the difference decides which papers a run
looks at:

| Identifier | Covers | Watch for |
|---|---|---|
| **ISSN** | Exactly one journal. | Nothing — this is the signal to prefer. A record whose title is abbreviated (`J Power Sources Adv`) still matches. |
| **Domain** | The host **and all its subdomains**, so a publisher's domain covers every journal it hosts. | Journals published on a platform. Whitelisting `sciencedirect.com` to get one Elsevier journal puts *all* of them in scope. |
| **Name** | Any venue name containing it as a whole-word phrase. | Short or generic titles. `"Sensors"` matches *IEEE Sensors Journal* and everything else with the word in it. |

Whitelisting a publisher domain or a publisher name on purpose — *check
everything IEEE published* — is a legitimate and common use. The caution is only
against reaching for one when a single journal was meant.

## Note on the word "whitelist"

It does **not** mean "venues I trust, so don't flag them". It is a *scope
filter*: it decides which papers are examined at all. A paper outside it comes
back `out_of_scope`, which means **not examined** — not `clean`, and not
cleared. Whitelisting a venue never suppresses a finding about it; a whitelisted
venue that is on Beall's List still comes back `on_list`.

## Blacklist semantics (extra predatory entries)

Blacklist entries become real snapshot entries in the `BeallIndex`, so they flow
through the exact same matching tiers as Beall's own lists (see
[`match.md`](../match/README.md)): domain, ISSN, exact name (with the ≥ 2-word rule), and
fuzzy name. `blacklist` outranks every Beall list when several entries share a
domain — the user explicitly asserted it — and it is eligible for fuzzy near-name
matching.

## Web app

Step 3 edits each list as a **list of rows**, built with a single box: type a
name, a website or an ISSN and press Add. On the whitelist, typing also searches
the [DOAJ](https://doaj.org) as you go and offers its journals underneath, so one
can be added with its official title and ISSN without either being looked up —
choosing a match adds it. If nothing matches, Add takes the text as typed. Each
row states which of the three identifiers it carries, because that is what
decides the run's scope.

Adding from DOAJ contributes the title and ISSN but **not** the journal's
website, since a domain is matched by host and many journals share a publisher
platform. A publisher domain is added deliberately, by typing it into the same
box.

The JSON is still there, in an **Edit as JSON** fold, in this same format and
synced both ways, so a list can still be pasted in or copied out. The app writes
your uploaded papers to a temporary folder and passes the lists through as the
same CLI flags, so behaviour is identical to the command line. See
[`ui/README.md`](../../../ui/README.md).

> DOAJ is used only to *name* a venue. Being indexed in DOAJ is not part of any
> verdict, and it is offered on the whitelist alone — appearing in a directory of
> vetted journals is evidence a journal was screened, which is no reason to add
> it to a blacklist.
