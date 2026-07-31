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

- **`--whitelist` selects the corpus.** Only papers whose venue matches a
  whitelisted entry are checked, and only they are in the workbook — every other
  paper is dropped before the run and appears in no sheet, so every count in the
  report is out of the papers that were kept. How many were excluded is on the
  Summary sheet. An empty or absent whitelist means "check everything" (the
  default).
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

## One entry, one identifier

**Each entry is matched on the most precise identifier it carries** — ISSN, else
domain, else name (`match.user_entry_signals`) — and an ISSN entry also accepts
an **exact whole-name** match, for the records that carry no ISSN of their own.
It governs both lists.

A directory match attaches a journal's official title *and* its ISSN, and letting
the title match as well turns one journal into every venue whose name contains
that phrase. Measured over the corpora in `docs/`: a *Neural Networks* entry
matched by its title reaches **114 papers across 11 venues** — a BlackboxNLP
workshop, *IEEE Transactions on Neural Networks and Learning Systems*, four
conferences — of which 94 are in venues nobody named. Matched by its ISSN it
reaches the **20 papers in the journal**, and misses none of them.

An ISSN is to a journal what a DOI is to an article: a registered identifier for
one thing. A name is a description, and descriptions are shared.

**But 42% of the records in those corpora carry no venue ISSN**, and one naming
*Neural Networks* without one is still in the journal that was named. So an ISSN
entry also matches a venue whose name is *exactly* its own. That is not what
over-matched: of the 94 unwanted papers none is in a venue named exactly that,
and 83 of them carry no ISSN, so the fallback recovers the missing records
without letting any of them back in. Phrase containment stays reserved for an
entry whose only identifier is a name — someone who types "IEEE" means the
breadth.

## Whitelist semantics (scope filter)

A paper is *in scope* when the entry's own signal matches:

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

It does **not** mean "venues I trust, so don't flag them". It *selects the
corpus*: it decides which papers are examined at all. A paper outside it is not
examined and is not in the report, and its absence is not a verdict — it is not
`clean`, and it is not cleared. Whitelisting a venue never suppresses a finding
about it; a whitelisted venue that is on Beall's List still comes back `on_list`.

## Blacklist semantics (extra predatory entries)

Blacklist entries become real snapshot entries in the `BeallIndex`, so they flow
through the same matching tiers as Beall's own lists (see
[`match.md`](../match/README.md)): domain, ISSN, exact name (with the ≥ 2-word rule), and
fuzzy name. `blacklist` outranks every Beall list when several entries share a
domain — the user explicitly asserted it — and it is eligible for fuzzy near-name
matching.

**They are also asked first, before the tool judges anything.** `BeallIndex`
indexes the blacklist a second time on its own (`user_index`), and `match_paper`
consults it before the preprint / no-venue rule. Without that, blacklisting a
preprint server did nothing whatever: `arxiv.org` returns `no_venue` several
steps before any matching happens, so papers from arXiv came back unflagged and
nothing said the entry had been ignored. A blacklist entry is an instruction from
the person running the check, and an instruction the tool's own judgement can
overrule is not an instruction.

That early pass matches on the listed host or a host under it, an exact ISSN, or
the listed **name as a whole phrase inside the venue's name** — the same rule the
whitelist uses, and the one each row's label promises. Exact equality was the
rule at first and no real catalogue survives it: Semantic Scholar records one
journal as *ACM Transactions on Multimedia Computing, Communications, and
Applications (TOMCCAP)*, so an entry typed without the trailing abbreviation
matched only by the accident of that record carrying an alternate name without
it. Phrases are padded, so word boundaries hold: `Sensors` catches *IEEE Sensors
Journal*, never *Biosensors*.

Unlike the Beall path it does **not** apply the name-corroboration downgrade. That rule exists because a third-party list's name
may coincide with a different journal; it has no business overruling a name this
user chose deliberately. A blacklist hit is therefore `on_list`, and the row says
in words that it came from the reader's own list and on which signal.

## Web app

Step 3 edits each list as a **list of rows**, built with a single box: type a
name, a website or an ISSN and press Add. Typing also searches a venue directory as you
go and offers its venues underneath, so one can be added with its official title
and ISSN without either being looked up — choosing a match adds it. The whitelist
searches the [DOAJ](https://doaj.org); the blacklist searches the
[Crossref](https://www.crossref.org) journal registry, since a venue worth
blacklisting is by definition not in a directory of vetted journals, while
Crossref screens nobody — a publisher is in it because it pays to register DOIs.
If nothing matches, Add takes the text as typed. Each
row reads `matches on · ISSN 1234-5678 · venue name`, naming each identifier it
carries and its value, because that is what decides the run's scope. Hovering a
pill says what that signal will match and how widely.

Adding from either directory contributes the title and ISSN but **not** the
journal's website, since a domain is matched by host and many journals share a
publisher platform. (Crossref's journal records carry no website in any case.) A
publisher domain is added deliberately, by typing it into the same box.

The JSON is still there, on the box's **JSON** tab, in this same format
and synced both ways, so a list can still be pasted in or copied out. The app writes
your uploaded papers to a temporary folder and passes the lists through as the
same CLI flags, so behaviour is identical to the command line. See
[`ui/README.md`](../../../ui/README.md).

> Both directories are used only to *name* a venue. Being listed in either is not
> part of any verdict: DOAJ membership says a journal passed a screening, and a
> Crossref entry says only that someone paid to register its DOIs. Neither
> answers the question this tool asks.
