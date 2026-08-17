# Multi-list support — developer notes

A running audit trail for the work that prepares the check to consult more than
one predatory-venue list. One entry per phase, written as the phase lands.

The guiding constraint throughout: **the existing Beall-only behaviour is the
compatibility baseline.** A change is acceptable only if it either preserves that
behaviour exactly, or is a correction whose every effect is enumerated below.

---

## Phase 1 — Safety baseline

### Changed

- **Added** `tests/test_report_golden.py`, `tests/golden/report_baseline.json`,
  `tests/README.md`.
- **No production code touched.**

### Why

The project had no tests. Nothing pinned the workbook's cell values, merge ranges,
row heights or status colours, so any change to matching or wording could regress
the report silently. That was the largest risk to "do not lose current
functionality", and it had to be closed before anything else moved.

### Compatibility

Nothing changes. The suite is additive and imports the package through the same
`sys.path` shim the package's own entry scripts use.

### Tests

Added 4 tests in two classes:

- `TestMatcherVerdicts` — asserts the verdict each of the 14 fixture papers must
  reach, that all five list categories are exercised, and that the joined
  `list_source` case is present.
- `TestWorkbookGolden` — cell-level comparison of the generated workbook against
  a recorded baseline.

```bash
python -m unittest discover -s tests -t tests -v      # 4 tests, OK
```

Verified the suite is worth having before trusting it: two independent dumps are
byte-equal (deterministic), and perturbing a single user-visible string makes it
fail (sensitive). It captures 5 sheets, 60 merge ranges on Results alone, 58 row
heights, and all five status fill colours.

### Known risks

- **The DOI resolvers have no coverage.** `resolve.py` and the Crossref branch of
  `data_quality.py` are network-bound and excluded by construction, so the test
  calls `match_paper` directly and supplies `data_quality` itself. Phase 3 touches
  `resolve.py` and will need its own tests.
- Cell-level, not byte-level, and deliberately so: two saves of an identical
  workbook differ in their bytes (ZIP member timestamps, `docProps/core.xml`), so
  byte-identity was never an achievable acceptance test.
- The fixture is synthetic, so it cannot catch a regression that only the real
  snapshot's data shape would expose.

---

## Phase 2 — Pure bug fixes

Three confirmed defects, all of them the same root cause: `list_source` can name
**several** lists joined with `"; "` (the alternate-URL tier in `match.py` does
this), and most consumers compared the field whole as though it always named one.

### Changed

| File | Change |
|---|---|
| `normalize.py` | **New** `split_list_sources()` — the one place that splits the field. |
| `match/match.py` | `_review_reason` reads the field through the splitter; line 370's `== "hijacked"` becomes a test on the *first* list; line 417's inline `.split("; ")` now uses the shared helper (behaviour-identical). |
| `report.py` | `_match_subrows`' link description tests the *first* list instead of the whole field; `_write_summary_sheet`'s counter splits before counting. |

No new module dependencies in the wrong direction: `normalize.py` is a leaf
utility that `match.py` already imported, and `report.py` now imports it too.

**Why the *first* list rather than a membership test.** `match.py` builds the
joined string and `matched_url` from the same ordered list of hits
(`dict.fromkeys` preserves first-occurrence order), so `sources[0]` is always the
list of the entry the link points at. A membership test would have been wrong in
the reverse ordering — it would call a publisher's site a counterfeit. The two
questions in `_review_reason` are genuinely different and are now written as
such: *which entry is this link about* (first) versus *is any matched list a weak
one* (membership).

### Why

1. **`report.py` link description — live bug.** A row whose `list_source` was
   `'hijacked; publishers'` matched neither `== "hijacked"` nor `== "blacklist"`,
   so it fell through and described a **counterfeit journal's website** as "this
   Beall-listed venue's own website".
2. **`report.py` Summary counter — live bug.** The counter keyed on the whole
   field while the renderer beside it (`descriptions._list_plain`) split it, so a
   joined value became a bucket of its own — a Summary line reading *"hijacked /
   cloned journal; predatory publisher"* whose count belonged to neither list.
3. **`match.py:370` — latent, not live.** Same exact-equality pattern, and
   inconsistent with line 417 in the *same function*. It could not fire with a
   joined value today, because joined values only arise with
   `matched_on == "domain_alternate_url"`, which does not start with `"name"`.
   Fixed for consistency; it produces **no output change**, which the golden test
   confirms.

### Compatibility

**Unchanged:** every single-list row, on every sheet. The first-list rule is
identical to the old whole-field comparison whenever the field names one list,
which is every row the alternate-URL tier did not produce.

**End-to-end confirmation** — the real example corpus, offline:

```
on_list: 1   review: 3   clean: 2   no_venue: 2
```

exactly the published expectation in [`examples/README.md`](examples/README.md).
The batch CSV path gives the same counts. All five entry points still import.

**Intentional differences — 28 cells, all of them consequences of the two live
fixes:**

- **3 cells** (Results!F39, Flagged only!F23, Data quality!F3 — the same paper on
  three sheets): the link description now reads *"this hijacked (counterfeit)
  journal's own website"*. This is the fix.
- **25 cells on Summary**: the composite row *"hijacked / cloned journal;
  predatory publisher"* (count 1) is gone; `predatory publisher` goes 2 → 3 and
  `hijacked / cloned journal` 1 → 2. Every other changed cell is the
  one-row upward shift that removing a row causes — `max_row` 36 → 35.

**A consequence worth knowing:** a paper on two lists now counts under each, so
the per-list figures can sum to more than the flagged total (11 vs 10 in the
fixture). That is the correct reading of "how many papers did each list flag",
but the section header still says *"Flagged papers by Beall list"*. Renaming it is
wording, which is Phase 5 — recorded here so it is not lost.

### Tests

```bash
python -m unittest discover -s tests -t tests -v      # 4 tests, OK
```

Baseline re-recorded after enumerating every diff above. No tests added this
phase — Phase 1's suite is what made these changes reviewable.

### Known risks

- `descriptions._list_plain` still splits the field with its own inline
  expression rather than the shared helper. Consolidating it would mean adding a
  `sys.path` shim to `descriptions.py`, which is an architectural change this
  phase was told not to make. The two implementations agree today; recorded as
  debt.
- The Summary section header is now slightly understated (see above). Phase 5.

---

## Phase 3, step 1 — the provider seam

Scaffolding only. **No second catalogue is added**, no CLI flag, no UI change.

### Changed

| File | Change |
|---|---|
| `config.py` | **New** `PROVIDERS` registry, `DEFAULT_PROVIDERS = ("beall",)`, `PROVIDER_RANK`, `KNOWN_LIST_SOURCES`. `SNAPSHOT_PATH` kept as an alias so existing imports are untouched. |
| `bealls_list_check.py` | `load_snapshot(provider="beall")`; `build_index(blacklist_path=None, *, providers=None)`; new `_validate_entries` and `_merged_meta`. |
| `user_lists.py` | User entries stamped `provider="user"`. |
| `match/match.py` | `_best` gains an explicit provider tie-break; `provider` and `matched_entry` added to the result dict and set by `_hit`. |
| `resolve/resolve.py` | `_resolve_name_exact` confirms against the entry that matched, falling back to the old lookup only if absent. |

### Why

**A separate `provider` field, never a namespaced `list_source`.** `list_source`
is compared by exact value in five places. Namespacing it (`"kscien:hijacked"`)
would silently break all five — including the one comparison that stops a
vanity-press or fake-metrics match being asserted as a confident hit, and the
gate that lets a hijacked review be cleared by its DOI. Three of those four
failures are in the over-assertion direction, and none of them errors.

**Filtering at index-build time, not match time.** `domain_match` returns on the
first suffix that is a key, so filtering inside a bucket would abandon the
longest-suffix walk early and call `_best([])` → `min()` on empty → an `error`
row per paper. Nine call sites in `resolve.py` and `bealls_llm_check.py` hold the
same index and get the right universe for free from a load-time filter.

**The blacklist is concatenated after the provider entries**, so no selection can
drop it. A predicate with an "except the user's own" clause is one refactor away
from being forgotten, and forgetting it is silent: `user_index` becomes `None`,
the whole blacklist tier stops running, and the run still prints that the entries
were added.

**`snapshot["meta"]` stays a dict.** Six sites call `.get()` on it and four of
those run at the very end of a run. It now carries a `providers` list inside, and
`scraped_utc` reports the *oldest* date across the catalogues used — a report is
only as current as the stalest list behind it.

### Compatibility

Unchanged. `build_index()` with no arguments loads Beall's alone, exactly as
before; the return tuple and meta shape are identical. The golden workbook is
cell-for-cell unchanged, and the real example corpus is still
`1 on_list, 3 review, 2 clean, 2 no_venue`.

The two new result fields (`provider`, `matched_entry`) are written but not yet
rendered, so no workbook cell moves.

### Tests

15 new tests in `tests/test_providers.py`; 19 total.

```bash
python -m unittest discover -s tests -t tests -v      # 19 tests, OK
```

Two previously untested paths are now pinned:

- **The ISSN tier.** None of the 3,045 shipped Beall entries carries an ISSN, so
  this tier has only ever fired for the user's own entries. A test asserts that
  fact, so if it ever stops being true the blast radius is visible. Another test
  documents the asymmetry deliberately: a *name* match against an entry with a
  website is downgraded to `review` unless the paper's own site agrees, while an
  *ISSN* match is asserted with no such check.
- **The DOI confirmation of a name match.** Measured honestly: **0 of 3,045**
  entries have a stored `normalized_name` differing from `normalize_name(name)`,
  so the old re-derivation returned the same entry and this was latent, not a
  live misreport. Demonstrated failure mode when two catalogues share a title:
  the old code picked the other catalogue's entry, whose ISSN did not match, and
  **missed the promotion** — `review` instead of `on_list`.

### Known risks

- No second catalogue exists yet, so the registry has one implementation and the
  `providers=` argument has no caller. That is deliberate for this step.
- `predatory_conference` is **not** yet in `KNOWN_LIST_SOURCES`; it is added with
  Kscien in step 2, together with the weak-list rule that keeps it review-only.
- `matched_entry` holds a reference to the snapshot entry. Nothing serialises the
  result dict, so this is safe, but a future writer must not render it.

---

## Phase 3, step 2 — say why a paper is not in the legitimate set

### Changed

`match/match.py` records which of the two `no_venue` causes applied, in
`matched_on`: `"preprint"` or `"no_venue_metadata"`. `descriptions.py` gains
`_NO_VENUE_NOTES` and `_nonmatch_note(r)`; `report.py` calls the latter.

### Why

`no_venue` covers two different findings and stated them as one undecided
sentence — *"this is a preprint, **or** the record has no venue information"* —
leaving the reader to work out which had happened. Once the workbook reports a
legitimate set, every paper outside it has to say why it is outside, and these
are different answers: a preprint has a venue that no predatory-venue list can
cover, while a record with no venue fields could not be examined at all. The
second is a metadata gap the user can fix by supplying the venue or the DOI.

Both keep the `no_venue` **status**. The batch CSV's five documented status
values are a published integration contract, so a sixth status would be a
breaking change for something that is a difference of explanation, not of verdict.

### Compatibility

Statuses, `list_source` values, flagged count, merge ranges, column widths and
`max_row` are all unchanged. **3 cells** change — the two new notes, one of which
appears on the Data-quality sheet as well — plus the row heights of 8 rows, because
the new text is longer and wraps taller.

### Tests

3 new tests; 22 total. They pin that the two causes are distinguished, that each
renders its own note, that neither note reads as a judgement on the paper, and
that a future `no_venue` branch which forgets to set `matched_on` still renders
something rather than an empty cell.

### Known risks

The Legend's status table still describes `no_venue` as one combined case. That
is wording, and belongs with the rest of the wording pass.
